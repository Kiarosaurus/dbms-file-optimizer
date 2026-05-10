from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.schema import FieldType, RecordSerializer, SchemaField
from core.storage import PAGE_SIZE
from indexing.bplus_tree import (
    BPlusTreeIndex,
    LEAF_HDR_SIZE,
    NO_NEXT,
    NODE_LEAF,
)
from indexing.sequential_file import SequentialIndex
from indexing.extendible_hash import ExtendibleHashIndex
from indexing.rtree import RTreeIndex
from parsing.ast_nodes import (
    AggregateExpr,
    CreateSchemaNode,
    DeleteNode,
    FilterExpr,
    InstructionNode,
    InsertNode,
    JoinClause,
    QueryNode,
    SpatialFilterExpr,
)

from engine.operations import hash_join, merge_join, nested_loop_join
from engine.external_sort import ExternalSorter
from core.concurrency import atomic_transaction, table_lock

# rows over este umbral usan ExternalSort en vez de hash join para acotar memoria
_EXTERNAL_SORT_THRESHOLD = 500


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "INTEGER": "INTEGER", "INT": "INTEGER",
    "BIGINT":  "BIGINT",
    "FLOAT":   "FLOAT",   "REAL": "FLOAT", "DOUBLE": "FLOAT",
    "STRING":  "STRING",  "VARCHAR": "STRING", "TEXT": "STRING", "CHAR": "STRING",
}


# construye un RecordSerializer desde la lista de fields del catalog
def _build_serializer(fields_meta: List[Dict]) -> RecordSerializer:
    schema_fields = []
    for f in fields_meta:
        ft = FieldType[f["type"]]
        sf = SchemaField(name=f["name"], field_type=ft, str_size=f.get("str_size"))
        schema_fields.append(sf)
    return RecordSerializer(schema_fields)


# convierte los column_specs del AST a la lista de dicts que guarda el catalog
def _fields_from_create(node: CreateSchemaNode) -> List[Dict]:
    result = []
    for col in node.column_specs:
        ft = _TYPE_MAP.get(col.data_type.upper(), "STRING")
        entry: Dict[str, Any] = {"name": col.col_name, "type": ft}
        if ft == "STRING":
            entry["str_size"] = col.size or 50
        result.append(entry)
    return result


# retorna la primera columna FLOAT que venga DESPUÉS de x_col — usada para el par (X,Y) del RTree
def _find_companion_col(fields_meta: List[Dict], x_col: str) -> str:
    found = False
    for f in fields_meta:
        if found and f["type"] == "FLOAT":
            return f["name"]
        if f["name"] == x_col:
            found = True
    raise ValueError(
        f"R-Tree index on '{x_col}' requires a second FLOAT column immediately after it in the schema"
    )


# ---------------------------------------------------------------------------
# Page-by-page table scanners (generators)
# ---------------------------------------------------------------------------

# scan completo de un SequentialIndex leyendo página a página (main + overflow)
def seq_full_scan(index: SequentialIndex) -> Iterator[Dict]:
    for path in (index.main_path, index.overflow_path):
        count = index._read_count(path)
        if count == 0:
            continue
        n_pages = (count + index.records_per_page - 1) // index.records_per_page
        remaining = count
        for page_num in range(n_pages):
            block_id = page_num + 1          # block 0 is the header
            page = index._load_page(path, block_id)
            recs_this_page = min(index.records_per_page, remaining)
            for slot in range(recs_this_page):
                off = slot * index.record_size
                raw = bytes(page[off: off + index.record_size])
                yield index.serializer.deserialize(raw)
            remaining -= recs_this_page


# scan completo de un ExtendibleHashIndex leyendo cada bucket único una sola vez
def hash_full_scan(index: ExtendibleHashIndex) -> Iterator[Dict]:
    seen: set = set()
    for bucket_id in index.directory:
        if bucket_id in seen:
            continue
        seen.add(bucket_id)
        page = index._read_page(bucket_id)
        _, n = index._bucket_header(page)
        for i in range(n):
            yield index._bucket_rec(page, i)


# scan completo de una hoja B+Tree siguiendo el linked list de leaves
def btree_full_scan(tree: BPlusTreeIndex) -> Iterator[Dict]:
    root_id, _ = tree._read_meta()

    # baja hasta la hoja más a la izquierda tomando child[0] en cada nivel
    current = root_id
    while True:
        page = tree._read_page(current)
        if tree._is_leaf(page):
            break
        current = tree._int_child(page, 0)

    # recorre la cadena de hojas hasta NO_NEXT
    while True:
        page = tree._read_page(current)
        n_records, next_leaf = tree._leaf_header(page)
        for i in range(n_records):
            yield tree._read_leaf_rec(page, i)
        if next_leaf == NO_NEXT:
            break
        current = next_leaf


# ---------------------------------------------------------------------------
# Filter evaluation
# ---------------------------------------------------------------------------

# chequeo de distancia euclidiana — usa columnas explícitas si hay dos, sino heurístico
def _eval_spatial_filter(record: Dict, expr: "SpatialFilterExpr") -> bool:
    import math as _math
    x_val = y_val = None
    if len(expr.columns) >= 2:
        # lookup directo con los nombres de columna del query
        x_col = expr.columns[0].split(".")[-1]
        y_col = expr.columns[1].split(".")[-1]
        for k, v in record.items():
            kb = k.split(".")[-1]
            if kb == x_col:
                x_val = v
            if kb == y_col:
                y_val = v
    else:
        # single-col fallback — busca por heurístico de nombres
        bare = expr.columns[0].split(".")[-1]
        for k, v in record.items():
            kb = k.split(".")[-1]
            if kb in ("x", "lon", "longitude", bare + "_x"):
                x_val = v
            if kb in ("y", "lat", "latitude", bare + "_y"):
                y_val = v
    if x_val is None or y_val is None:
        return False
    return _math.sqrt((float(x_val) - expr.cx) ** 2 +
                      (float(y_val) - expr.cy) ** 2) <= expr.radius


# evalúa un FilterExpr recursivo contra un registro
def _eval_filter(record: Dict, expr) -> bool:
    if isinstance(expr, SpatialFilterExpr):
        return _eval_spatial_filter(record, expr)
    if isinstance(expr.left_operand, FilterExpr):
        left  = _eval_filter(record, expr.left_operand)
        right = _eval_filter(record, expr.right_operand)
        return (left and right) if expr.operator == "AND" else (left or right)

    col = str(expr.left_operand)
    val = record.get(col)
    if val is None:
        # fallback al bare field name cuando el record no trae prefijo tabla
        bare = col.split(".")[-1]
        for k, v in record.items():
            if k.split(".")[-1] == bare:
                val = v
                break

    rhs = expr.right_operand
    op  = expr.operator
    # coerce string rhs to the stored type when comparing numeric fields
    if isinstance(val, (int, float)) and isinstance(rhs, str):
        try:
            rhs = type(val)(rhs)
        except (ValueError, TypeError):
            pass
    if op in ("=", "=="):  return val == rhs
    if op == "!=":         return val != rhs
    if op == "<":          return val < rhs
    if op == ">":          return val > rhs
    if op == "<=":         return val <= rhs
    if op == ">=":         return val >= rhs
    return False


# detecta patrón BETWEEN desugared: FilterExpr(col>=lo, AND, col<=hi)
# retorna (lo, hi) si col bare-name == key_field; None si no aplica
def _extract_between(
    expr: Optional[FilterExpr], key_field: str
) -> Optional[Tuple[Any, Any]]:
    if expr is None or not isinstance(expr.left_operand, FilterExpr):
        return None
    if expr.operator != "AND":
        return None
    left  = expr.left_operand
    right = expr.right_operand
    # ambos lados deben ser comparaciones simples (no anidadas)
    if isinstance(left.left_operand, FilterExpr) or isinstance(right.left_operand, FilterExpr):
        return None
    ge = left  if left.operator == ">=" else (right if right.operator == ">=" else None)
    le = right if right.operator == "<=" else (left  if left.operator  == "<=" else None)
    if ge is None or le is None:
        return None
    if str(ge.left_operand).split(".")[-1] != key_field:
        return None
    if str(le.left_operand).split(".")[-1] != key_field:
        return None
    return (ge.right_operand, le.right_operand)


# true si todas las columnas del expr pertenecen a table_name (para push-down)
def _can_pushdown(expr: FilterExpr, table_name: str) -> bool:
    if isinstance(expr.left_operand, FilterExpr):
        return (
            _can_pushdown(expr.left_operand,  table_name)
            and _can_pushdown(expr.right_operand, table_name)
        )
    col = str(expr.left_operand)
    if "." in col:
        tbl, _ = col.split(".", 1)
        return tbl.lower() == table_name.lower()
    return True   # unqualified column -> assume belongs to this table

def _apply_alias(rows, table: str, alias: str):
    """Re-key rows: replace table. prefix with alias. prefix."""
    old = table + "."
    new = alias + "."
    for row in rows:
        yield {(new + k[len(old):] if k.startswith(old) else k): v for k, v in row.items()}

# parte el WHERE en filtros push-down por tabla y post-join
def _split_filter(
    expr: Optional[FilterExpr],
    left_table: str,
    right_table: str,
) -> Tuple[Optional[FilterExpr], Optional[FilterExpr], Optional[FilterExpr]]:
    if expr is None:
        return None, None, None

    # cadena AND: intenta rutear cada rama a su tabla; empuja ambas si es posible
    if isinstance(expr.left_operand, FilterExpr) and expr.operator == "AND":
        for branch, other in [(expr.left_operand, expr.right_operand),
                               (expr.right_operand, expr.left_operand)]:
            if _can_pushdown(branch, left_table):
                if _can_pushdown(other, right_table):
                    return branch, other, None   # ambas ramas se pueden empujar
                return branch, None, other
            if _can_pushdown(branch, right_table):
                if _can_pushdown(other, left_table):
                    return other, branch, None   # ambas ramas se pueden empujar
                return None, branch, other

    # expresión simple: asigna a la tabla que le corresponde
    if _can_pushdown(expr, left_table):
        return expr, None, None
    if _can_pushdown(expr, right_table):
        return None, expr, None
    return None, None, expr


# ---------------------------------------------------------------------------
# StorageEngine
# ---------------------------------------------------------------------------

class StorageEngine:

    # inicializa el engine con el directorio base y carga el catalog JSON
    def __init__(self, base_dir: str):
        self.data_dir     = base_dir
        self.catalog_path = os.path.join(base_dir, "catalog.json")
        self._catalog: Dict[str, Any] = self._load_catalog()
        self._index_cache: Dict[str, Any] = {}
        self._rtree_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Catalog persistence
    # ------------------------------------------------------------------

    # lee catalog.json o devuelve estructura vacía si no existe
    def _load_catalog(self) -> Dict:
        if os.path.exists(self.catalog_path):
            with open(self.catalog_path) as f:
                return json.load(f)
        return {"tables": {}}

    # persiste el catalog en disco con indent=2
    def _save_catalog(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.catalog_path, "w") as f:
            json.dump(self._catalog, f, indent=2)

    # ------------------------------------------------------------------
    # Index access
    # ------------------------------------------------------------------

    # retorna el index object cacheado o lo instancia desde el catalog
    def _get_index(self, table_name: str):
        if table_name in self._index_cache:
            return self._index_cache[table_name]

        meta = self._catalog["tables"].get(table_name)
        if meta is None:
            raise KeyError(f"Table '{table_name}' not found in catalog")

        serializer = _build_serializer(meta["fields"])
        idx_type   = meta.get("index", "SEQUENTIAL")

        if idx_type == "BTREE":
            index = BPlusTreeIndex(
                meta["path"], serializer,
                key_field=meta["index_col"],
            )
        elif idx_type == "HASH":
            index = ExtendibleHashIndex(
                file_path=meta["path"],
                serializer=serializer,
                key_field=meta["index_col"],
            )
        else:
            index = SequentialIndex(
                main_path=meta["main_path"],
                overflow_path=meta["overflow_path"],
                serializer=serializer,
                K_threshold=meta.get("k_threshold", 50),
            )

        self._index_cache[table_name] = index
        return index

    # retorna el RTreeIndex cacheado para la tabla; lo instancia si no existe aún
    def _get_rtree(self, table_name: str) -> RTreeIndex:
        if table_name not in self._rtree_cache:
            meta = self._catalog["tables"][table_name]
            self._rtree_cache[table_name] = RTreeIndex(meta["rtree_path"])
        return self._rtree_cache[table_name]

    # flush del overflow al main antes de un merge join en SequentialIndex
    def _ensure_sorted(self, table_name: str) -> None:
        meta = self._catalog["tables"][table_name]
        if meta.get("index", "SEQUENTIAL") == "SEQUENTIAL":
            self._get_index(table_name).reorganize()

    # ------------------------------------------------------------------
    # Table scan (qualified records, page-by-page)
    # ------------------------------------------------------------------

    # scan página a página emitiendo records con prefijo "TableName.field"
    def _scan_table(
        self,
        table_name: str,
        filter_expr: Optional[FilterExpr] = None,
    ) -> Iterator[Dict]:
        index    = self._get_index(table_name)
        meta     = self._catalog["tables"][table_name]
        idx_type = meta.get("index", "SEQUENTIAL")

        # BETWEEN push-down: usa range_search en vez de full scan para SEQ y BTREE
        if idx_type in ("SEQUENTIAL", "BTREE") and filter_expr is not None:
            key_field = meta.get("index_col", meta["fields"][0]["name"])
            between   = _extract_between(filter_expr, key_field)
            if between is not None:
                lo, hi = between
                for record in index.range_search(lo, hi):
                    yield {f"{table_name}.{k}": v for k, v in record.items()}
                return

        if idx_type == "BTREE":
            raw_iter = btree_full_scan(index)
        elif idx_type == "HASH":
            raw_iter = hash_full_scan(index)
        else:
            raw_iter = seq_full_scan(index)

        for record in raw_iter:
            qualified = {f"{table_name}.{k}": v for k, v in record.items()}
            if filter_expr is None or _eval_filter(qualified, filter_expr):
                yield qualified

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    # dispatcher principal: enruta cada InstructionNode a su handler
    def execute(self, node: InstructionNode):
        if isinstance(node, CreateSchemaNode):
            return self._exec_create(node)
        if isinstance(node, InsertNode):
            return self._exec_insert(node)
        if isinstance(node, QueryNode):
            return self._exec_query(node)
        if isinstance(node, DeleteNode):
            return self._exec_delete(node)
        raise ValueError(f"Unsupported node type: {type(node).__name__}")

    # ------------------------------------------------------------------
    # CREATE TABLE
    # ------------------------------------------------------------------

    # crea la estructura de archivos e inserta la entrada en el catalog
    def _exec_create(self, node: CreateSchemaNode) -> str:
        name = node.table_name
        if name in self._catalog["tables"]:
            return f"Table '{name}' already exists"

        fields_meta = _fields_from_create(node)

        idx_type = "SEQUENTIAL"
        idx_col  = fields_meta[0]["name"]
        for d in node.index_directives:
            idx_type = d.index_kind
            idx_col  = d.col_name
            break

        serializer = _build_serializer(fields_meta)

        if idx_type == "BTREE":
            path = os.path.join(self.data_dir, f"{name}_btree.bin")
            BPlusTreeIndex(path, serializer, key_field=idx_col)
            entry: Dict[str, Any] = {
                "fields":    fields_meta,
                "index":     "BTREE",
                "index_col": idx_col,
                "path":      path,
            }
        elif idx_type == "HASH":
            path = os.path.join(self.data_dir, f"{name}_hash.bin")
            ExtendibleHashIndex(path, serializer, key_field=idx_col)
            entry = {
                "fields":    fields_meta,
                "index":     "HASH",
                "index_col": idx_col,
                "path":      path,
            }
        elif idx_type == "RTREE":
            # main store = sequential, más un RTree secundario para queries espaciales
            main_path     = os.path.join(self.data_dir, f"{name}_main.bin")
            overflow_path = os.path.join(self.data_dir, f"{name}_overflow.bin")
            rtree_path    = os.path.join(self.data_dir, f"{name}_rtree.bin")
            SequentialIndex(main_path, overflow_path, serializer, K_threshold=50)
            RTreeIndex(rtree_path)
            companion_col = _find_companion_col(fields_meta, idx_col)
            entry = {
                "fields":        fields_meta,
                "index":         "SEQUENTIAL",   # _get_index sigue usando el seq store
                "index_col":     idx_col,
                "main_path":     main_path,
                "overflow_path": overflow_path,
                "k_threshold":   50,
                "rtree_path":    rtree_path,      # señala que esta tabla tiene indice espacial
                "rtree_col":     idx_col,         # columna X
                "rtree_col_y":   companion_col,   # columna Y (siguiente FLOAT en el schema)
            }
        else:
            main_path     = os.path.join(self.data_dir, f"{name}_main.bin")
            overflow_path = os.path.join(self.data_dir, f"{name}_overflow.bin")
            SequentialIndex(main_path, overflow_path, serializer, K_threshold=50)
            entry = {
                "fields":        fields_meta,
                "index":         "SEQUENTIAL",
                "index_col":     idx_col,
                "main_path":     main_path,
                "overflow_path": overflow_path,
                "k_threshold":   50,
            }

        self._catalog["tables"][name] = entry
        self._save_catalog()

        if node.from_file:
            csv_path = node.from_file
            if not os.path.isabs(csv_path) and not os.path.exists(csv_path):
                for candidate_dir in (self.data_dir, "data", os.path.join(os.path.dirname(self.data_dir), "data")):
                    candidate = os.path.join(candidate_dir, csv_path)
                    if os.path.exists(candidate):
                        csv_path = candidate
                        break
            loaded = self.massive_ingest(csv_path, name)
            return f"Table '{name}' created and loaded {loaded} rows from '{csv_path}'"
        return f"Table '{name}' created"

    # ------------------------------------------------------------------
    # INSERT INTO
    # ------------------------------------------------------------------

    # valida columnas, construye el record y lo inserta bajo write lock
    @atomic_transaction
    def _exec_insert(self, node: InsertNode) -> str:
        meta = self._catalog["tables"].get(node.table_name)
        if meta is None:
            raise KeyError(f"Table '{node.table_name}' not found")

        fields = meta["fields"]
        if len(node.value_list) != len(fields):
            raise ValueError(
                f"INSERT: {len(node.value_list)} values for {len(fields)} columns"
            )

        record = {fields[i]["name"]: v.raw_value for i, v in enumerate(node.value_list)}
        with table_lock.write_guard(node.table_name):
            self._get_index(node.table_name).add(record)
            # si hay rtree secundario, inserta el punto espacial usando el PK como record_id
            if meta.get("rtree_path"):
                x_val = float(record.get(meta["rtree_col"],   0.0))
                y_val = float(record.get(meta["rtree_col_y"], 0.0))
                rid   = int(record.get(fields[0]["name"], 0))
                self._get_rtree(node.table_name).add((x_val, y_val), rid)
        return f"1 row inserted into '{node.table_name}'"

    # ------------------------------------------------------------------
    # DELETE FROM
    # ------------------------------------------------------------------

    # escanea con filtro, deduplica por PK y elimina cada match
    @atomic_transaction
    def _exec_delete(self, node: DeleteNode) -> str:
        meta = self._catalog["tables"].get(node.table_name)
        if meta is None:
            raise KeyError(f"Table '{node.table_name}' not found")

        # index_col: key used by the primary index for lookups/removes
        # rid_field: first field, used as record_id in the RTree
        index_col = meta.get("index_col", meta["fields"][0]["name"])
        rid_field = meta["fields"][0]["name"]
        prefix    = f"{node.table_name}."
        index     = self._get_index(node.table_name)
        rtree_idx = self._get_rtree(node.table_name) if meta.get("rtree_path") else None
        deleted   = 0

        # scan + delete inside the same write lock to prevent TOCTOU race
        with table_lock.write_guard(node.table_name):
            rows = list(self._scan_table(node.table_name, node.filter))
            seen: set = set()
            for row in rows:
                idx_val = row.get(f"{prefix}{index_col}")
                rid_val = row.get(f"{prefix}{rid_field}")
                if idx_val is not None and idx_val not in seen:
                    seen.add(idx_val)
                    deleted += index.remove(idx_val)
                    if rtree_idx is not None:
                        rtree_idx.remove(rid_val)

        return f"{deleted} row(s) deleted from '{node.table_name}'"

    # ------------------------------------------------------------------
    # SELECT (single-table)
    # ------------------------------------------------------------------

    # ejecuta un SELECT simple o delega a join si hay join_target
    @atomic_transaction
    def _exec_query(self, node: QueryNode) -> List[Dict]:
        if node.join_target:
            return self._exec_join_query(node)

        # consulta espacial: exige RTree index, ruta directa sin seq_full_scan
        if isinstance(node.filter, SpatialFilterExpr):
            meta = self._catalog["tables"].get(node.source, {})
            if "rtree_path" not in meta:
                raise ValueError(
                    f"La tabla '{node.source}' no tiene un índice espacial configurado"
                )
            # column pair validation: verifica que la query use las cols del rtree catalogado
            sf = node.filter
            if len(sf.columns) >= 2:
                qx = sf.columns[0].split(".")[-1]
                qy = sf.columns[1].split(".")[-1]
                rx = meta.get("rtree_col", "")
                ry = meta.get("rtree_col_y", "")
                if (qx, qy) != (rx, ry):
                    raise ValueError(
                        f"Columnas ({qx}, {qy}) no coinciden con el índice espacial ({rx}, {ry})"
                    )
            return self._exec_spatial_query(node)

        rows = list(self._scan_table(node.source, node.filter))

        if node.group_by_cols or node.aggregations:
            rows = self._aggregate(rows, node)

        if node.order_by_cols:
            def _sort_key(row: Dict) -> tuple:
                vals = []
                for col in node.order_by_cols:
                    v = row.get(col)
                    if v is None:
                        bare = col.split(".")[-1]
                        v = next((rv for rk, rv in row.items()
                                  if rk.split(".")[-1] == bare), None)
                    vals.append((v is None, v))   # None sorts last
                return tuple(vals)
            rows.sort(key=_sort_key)

        return self._project(rows, node.targets)

    # usa el RTree para filtrar record_ids y luego reconstruye registros completos del seq store
    def _exec_spatial_query(self, node: QueryNode) -> List[Dict]:
        sf     = node.filter
        source = node.source
        meta   = self._catalog["tables"][source]

        rtree = self._get_rtree(source)

        if sf.k > 0:  # KNN mode
            hits    = rtree.knn_query(sf.cx, sf.cy, sf.k)
            # distancia euclidiana: sqrt((x-cx)^2 + (y-cy)^2) — ya calculada por knn_query
            hit_ids = {rid for _, _, rid, _ in hits}
        else:          # RADIUS mode
            hits    = rtree.spatial_query(sf.cx, sf.cy, sf.radius)
            hit_ids = {rid for _, _, rid in hits}

        pk_name = meta["fields"][0]["name"]
        rows    = []
        for raw in seq_full_scan(self._get_index(source)):
            if raw.get(pk_name) in hit_ids:
                rows.append({f"{source}.{k}": v for k, v in raw.items()})

        # post-filter euclidiano con columnas explícitas cuando la query especifica (x, y)
        if sf.radius > 0 and len(sf.columns) >= 2:
            import math as _imath
            x_col = sf.columns[0].split(".")[-1]
            y_col = sf.columns[1].split(".")[-1]
            rows = [
                r for r in rows
                if _imath.sqrt(
                    (float(r.get(f"{source}.{x_col}", r.get(x_col, 0.0))) - sf.cx) ** 2 +
                    (float(r.get(f"{source}.{y_col}", r.get(y_col, 0.0))) - sf.cy) ** 2
                ) <= sf.radius
            ]

        return self._project(rows, node.targets)

    # ------------------------------------------------------------------
    # SELECT … JOIN
    # ------------------------------------------------------------------

    # elige estrategia de join: MERGE si ambos sorted, HASH si chico, EXTERNAL si grande
    def _exec_join_query(self, node: QueryNode) -> List[Dict]:
        jc          = node.join_condition
        left_table  = node.source
        right_table = node.join_target

        left_join_col  = jc.left_col   # qualified, e.g. "Estudiantes.ID"
        right_join_col = jc.right_col  # qualified, e.g. "Notas.ID_Est"

        # parte el WHERE en push-down izquierdo, derecho y post-join
        left_filter, right_filter, post_filter = _split_filter(
            node.filter, left_table, right_table
        )

        left_meta  = self._catalog["tables"][left_table]
        right_meta = self._catalog["tables"][right_table]

        left_pk   = left_meta["fields"][0]["name"]
        right_pk  = right_meta["fields"][0]["name"]
        left_bare  = left_join_col.split(".")[-1]
        right_bare = right_join_col.split(".")[-1]

        # sorted si la join key coincide con el PK de un índice ordenado
        left_sorted  = (left_bare  == left_pk  and left_meta["index"]  in ("SEQUENTIAL", "BTREE"))
        right_sorted = (right_bare == right_pk and right_meta["index"] in ("SEQUENTIAL", "BTREE"))

        # si hay aliases, ajustar join cols para que apunten a las filas re-keyadas
        src_alias = node.source_alias
        jt_alias  = node.join_alias
        left_join_col_final  = left_join_col.replace(left_table + ".", src_alias + ".", 1) if src_alias else left_join_col
        right_join_col_final = right_join_col.replace(right_table + ".", jt_alias + ".", 1) if jt_alias else right_join_col

        if left_sorted and right_sorted:
            # reorganiza overflow→main ANTES de escanear para garantizar orden en disco
            self._ensure_sorted(left_table)
            self._ensure_sorted(right_table)
            # scan directo como iteradores: merge_join consume sin materializar ambos lados
            strategy = "MERGE"
            left_iter  = _apply_alias(self._scan_table(left_table,  left_filter), left_table,  src_alias) if src_alias else self._scan_table(left_table,  left_filter)
            right_iter = _apply_alias(self._scan_table(right_table, right_filter), right_table, jt_alias)  if jt_alias  else self._scan_table(right_table, right_filter)
            rows = list(merge_join(left_iter, right_iter, left_join_col_final, right_join_col_final))
        else:
            # materializa ambos lados para decidir entre hash y external sort
            left_rows  = list(self._scan_table(left_table,  left_filter))
            right_rows = list(self._scan_table(right_table, right_filter))

            if (len(left_rows) > _EXTERNAL_SORT_THRESHOLD
                    or len(right_rows) > _EXTERNAL_SORT_THRESHOLD):
                # tablas grandes sin índice coincidente — external sort + merge join
                strategy = "EXTERNAL_SORT_MERGE"
                sorted_left  = self._sort_for_join(left_table,  left_rows,  left_bare,  left_join_col)
                sorted_right = self._sort_for_join(right_table, right_rows, right_bare, right_join_col)
                # re-keya después del sort (sort usa nombres reales de tabla)
                if src_alias:
                    sorted_left  = list(_apply_alias(iter(sorted_left),  left_table,  src_alias))
                if jt_alias:
                    sorted_right = list(_apply_alias(iter(sorted_right), right_table, jt_alias))
                rows = list(merge_join(
                    iter(sorted_left), iter(sorted_right),
                    left_join_col_final, right_join_col_final,
                ))
            else:
                # tablas chicas sin índice — hash join en memoria
                strategy = "HASH"
                if src_alias:
                    left_rows  = list(_apply_alias(iter(left_rows),  left_table,  src_alias))
                if jt_alias:
                    right_rows = list(_apply_alias(iter(right_rows), right_table, jt_alias))
                rows = list(hash_join(
                    iter(left_rows), iter(right_rows),
                    left_join_col_final, right_join_col_final,
                ))

        if post_filter:
            rows = [r for r in rows if _eval_filter(r, post_filter)]

        if node.group_by_cols or node.aggregations:
            rows = self._aggregate(rows, node)

        return self._project(rows, node.targets)

    # ------------------------------------------------------------------
    # GROUP BY + aggregation
    # ------------------------------------------------------------------

    # agrupa y aplica funciones de agregación (COUNT/SUM/AVG/MIN/MAX)
    def _aggregate(self, rows: List[Dict], node: QueryNode) -> List[Dict]:
        def _resolve(record: Dict, col: str) -> Any:
            v = record.get(col)
            if v is not None:
                return v
            bare = col.split(".")[-1]
            for k, vv in record.items():
                if k == bare or k.split(".")[-1] == bare:
                    return vv
            return None

        if node.group_by_cols:
            groups: Dict[tuple, List[Dict]] = {}
            for row in rows:
                key = tuple(_resolve(row, c) for c in node.group_by_cols)
                groups.setdefault(key, []).append(row)
        else:
            groups = {(): rows}

        result: List[Dict] = []
        for key, group_rows in groups.items():
            out: Dict[str, Any] = {}

            for col, val in zip(node.group_by_cols, key):
                out[col] = val

            for agg in node.aggregations:
                label  = f"{agg.func_name}({agg.argument})"
                values = [v for r in group_rows
                          if (v := _resolve(r, agg.argument)) is not None]
                if agg.func_name == "COUNT":
                    out[label] = len(group_rows)
                elif agg.func_name == "SUM":
                    out[label] = sum(values)
                elif agg.func_name == "AVG":
                    out[label] = (sum(values) / len(values)) if values else 0.0
                elif agg.func_name == "MIN":
                    out[label] = min(values) if values else None
                elif agg.func_name == "MAX":
                    out[label] = max(values) if values else None

            result.append(out)

        return result

    # ------------------------------------------------------------------
    # External sort helper (used by _exec_join_query)
    # ------------------------------------------------------------------

    # ordena las filas usando ExternalSorter para el join
    def _sort_for_join(
        self,
        table_name: str,
        qualified_rows: List[Dict],
        bare_sort_key: str,
        qualified_sort_key: str,
    ) -> List[Dict]:
        if not qualified_rows:
            return []

        meta       = self._catalog["tables"][table_name]
        serializer = _build_serializer(meta["fields"])
        prefix     = f"{table_name}."

        # strip del prefijo tabla para serializar, re-agrega después
        raw_rows = [
            {k[len(prefix):]: v for k, v in row.items()}
            for row in qualified_rows
        ]

        flat_in  = os.path.join(self.data_dir, f"_sjoin_{table_name}_in.bin")
        flat_out = os.path.join(self.data_dir, f"_sjoin_{table_name}_out.bin")

        sorter = ExternalSorter(serializer, bare_sort_key, self.data_dir)
        n      = sorter.records_to_flat_file(raw_rows, flat_in)
        sorter.sort_file(flat_in, flat_out, total_records=n)

        sorted_qualified = [
            {f"{table_name}.{k}": v for k, v in rec.items()}
            for rec in sorter.scan_sorted_file(flat_out, n)
        ]

        for p in (flat_in, flat_out):
            if os.path.exists(p):
                os.remove(p)

        return sorted_qualified

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    # proyecta las columnas del SELECT; fallback a bare name si no hay prefijo
    def _project(self, rows: List[Dict], targets: List[str]) -> List[Dict]:
        if targets == ["*"]:
            return rows

        result = []
        for row in rows:
            projected: Dict[str, Any] = {}
            for target in targets:
                val = row.get(target)
                if val is None:
                    # bare-name fallback para columnas sin calificar en el SELECT
                    bare = target.split(".")[-1]
                    for k, v in row.items():
                        if k == bare or k.split(".")[-1] == bare:
                            val = v
                            break
                projected[target] = val
            result.append(projected)

        return result

    # ------------------------------------------------------------------
    # Bulk CSV ingest
    # ------------------------------------------------------------------

    # carga masiva desde CSV; auto-crea tabla, infiere tipos y construye RTree si se piden cols espaciales
    def massive_ingest(
        self,
        csv_path: str,
        table_name: str,
        progress_cb=None,
        spatial_index_cols: Optional[List[str]] = None,
    ) -> int:
        import csv as _csv

        # Pasada 1: leer solo las primeras 100 filas para inferencia de schema.
        # Nunca se carga el archivo completo en memoria.
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
            _reader = _csv.DictReader(fh)
            sample_rows: List[Dict] = []
            for _row in _reader:
                sample_rows.append(_row)
                if len(sample_rows) >= 100:
                    break

        if not sample_rows:
            return 0

        headers = list(sample_rows[0].keys())

        # auto-crea la tabla infiriendo INTEGER -> FLOAT -> STRING desde las primeras muestras
        if table_name not in self._catalog["tables"]:
            fields_meta: List[Dict] = []
            for h in headers:
                col_name = h[:30].strip()
                if not col_name:
                    continue
                samples = [r[h].strip() for r in sample_rows[:20] if r[h].strip()]
                ft = "INTEGER"
                for v in samples:
                    try:
                        iv = int(v)
                        # 'i' a 'q': si supera int32, usar BIGINT para evitar overflow
                        if ft == "INTEGER" and not (-2_147_483_648 <= iv <= 2_147_483_647):
                            ft = "BIGINT"
                    except ValueError:
                        try:
                            float(v)
                            ft = "FLOAT"
                        except ValueError:
                            ft = "STRING"
                            break
                entry: Dict[str, Any] = {"name": col_name, "type": ft}
                if ft == "STRING":
                    max_len = max((len(r.get(h, "")) for r in sample_rows), default=10)
                    entry["str_size"] = min(max(max_len + 4, 20), 120)
                fields_meta.append(entry)

            main_p = os.path.join(self.data_dir, f"{table_name}_main.bin")
            ovfl_p = os.path.join(self.data_dir, f"{table_name}_ovfl.bin")
            ser    = _build_serializer(fields_meta)
            SequentialIndex(main_p, ovfl_p, ser, K_threshold=200)
            catalog_entry: Dict[str, Any] = {
                "fields":        fields_meta,
                "index":         "SEQUENTIAL",
                "index_col":     fields_meta[0]["name"],
                "main_path":     main_p,
                "overflow_path": ovfl_p,
                "k_threshold":   200,
            }
            # rtree build: registra el coordinate pair en el catalog
            if spatial_index_cols and len(spatial_index_cols) >= 2:
                col_set = {f["name"] for f in fields_meta}
                x_col, y_col = spatial_index_cols[0], spatial_index_cols[1]
                if x_col in col_set and y_col in col_set:
                    rtree_p = os.path.join(self.data_dir, f"{table_name}_rtree.bin")
                    RTreeIndex(rtree_p)
                    catalog_entry["rtree_path"]  = rtree_p
                    catalog_entry["rtree_col"]   = x_col
                    catalog_entry["rtree_col_y"] = y_col
            self._catalog["tables"][table_name] = catalog_entry
            self._save_catalog()

        # tabla ya existe: agrega rtree si se pide y aún no hay uno configurado
        elif spatial_index_cols and len(spatial_index_cols) >= 2:
            meta_ex = self._catalog["tables"][table_name]
            if "rtree_path" not in meta_ex:
                col_set = {f["name"] for f in meta_ex["fields"]}
                x_col, y_col = spatial_index_cols[0], spatial_index_cols[1]
                if x_col in col_set and y_col in col_set:
                    rtree_p = os.path.join(self.data_dir, f"{table_name}_rtree.bin")
                    RTreeIndex(rtree_p)
                    meta_ex["rtree_path"]  = rtree_p
                    meta_ex["rtree_col"]   = x_col
                    meta_ex["rtree_col_y"] = y_col
                    self._save_catalog()

        meta   = self._catalog["tables"][table_name]
        fields = meta["fields"]
        index  = self._get_index(table_name)

        # coordinate pair indexer: usa el cache del engine para coherencia con otras ops
        rtree_idx = self._get_rtree(table_name) if meta.get("rtree_path") else None
        pk_field  = fields[0]["name"]

        # coerce con fallback a cero/vacío para evitar errores de parsing
        def _coerce(fld: Dict, raw: str) -> Any:
            v = raw.strip() if isinstance(raw, str) else str(raw)
            if fld["type"] in ("INTEGER", "BIGINT"):
                try:    return int(float(v))
                except: return 0
            if fld["type"] == "FLOAT":
                try:    return float(v)
                except: return 0.0
            return v[: fld.get("str_size", 50)]

        # Cuenta total de filas para barra de progreso (solo newlines, sin parsing CSV)
        total_rows = 0
        if progress_cb:
            with open(csv_path, encoding="utf-8", errors="replace") as _fh:
                for _ in _fh:
                    total_rows += 1
            total_rows = max(0, total_rows - 1)  # descuenta el header

        # Pasada 2: insertar fila a fila sin materializar el CSV completo en RAM
        inserted = 0
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in _csv.DictReader(fh):
                record = {
                    fld["name"]: _coerce(fld, row.get(fld["name"], ""))
                    for fld in fields
                }
                index.add(record)
                if rtree_idx is not None:
                    x_val = float(record.get(meta["rtree_col"],   0.0))
                    y_val = float(record.get(meta["rtree_col_y"], 0.0))
                    rid   = int(record.get(pk_field, inserted + 1))
                    rtree_idx.add((x_val, y_val), rid)
                inserted += 1
                if progress_cb and inserted % 100 == 0:
                    progress_cb(inserted, total_rows)

        if progress_cb:
            progress_cb(inserted, total_rows)
        return inserted
