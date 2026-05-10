from __future__ import annotations

import math
import os
import re
import sys
import threading as _threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from core.concurrency import init_concurrency
from core.metrics import Telemetry
from engine.core import StorageEngine
from parsing.ast_nodes import QueryNode, SpatialFilterExpr
from parsing.sql_analyzer import QueryCompiler


# Rutas de datasets reales para carga automática del workspace default
_DATA_DEFAULT_DIR = os.path.join(_BASE, "data", "default")
_CSV_CANCIONES    = os.path.join(_DATA_DEFAULT_DIR, "song_data.csv")
_CSV_CIUDADES     = os.path.join(_DATA_DEFAULT_DIR, "wikipedia_cities_full_clean_143.csv")
_CSV_CONTESTS     = os.path.join(_DATA_DEFAULT_DIR, "contest_data.csv")
_CSV_PAISES       = os.path.join(_DATA_DEFAULT_DIR, "country_data.csv")


# Resumidor de mensajes SQL — agrupa inserciones y borrados por tabla
_INSERT_PAT = re.compile(r"1 row inserted into '(.+)'")
_DELETE_PAT = re.compile(r"(\d+) row\(s\) deleted from '(.+)'")


def _summarize_messages(msgs: List[str]) -> str:
    if not msgs:
        return "OK"
    if len(msgs) == 1:
        return msgs[0]

    insert_counts: Counter = Counter()
    delete_counts: Counter = Counter()
    other: List[str] = []

    for m in msgs:
        mi = _INSERT_PAT.fullmatch(m)
        if mi:
            insert_counts[mi.group(1)] += 1
            continue
        md = _DELETE_PAT.fullmatch(m)
        if md:
            delete_counts[md.group(2)] += int(md.group(1))
            continue
        other.append(m)

    parts: List[str] = []
    for tbl, n in insert_counts.items():
        parts.append(f"{n} row(s) inserted into '{tbl}'")
    for tbl, n in delete_counts.items():
        parts.append(f"{n} row(s) deleted from '{tbl}'")
    parts.extend(other)
    return "\n".join(parts)


# Resultado de query con metadata, datos y telemetría
@dataclass
class QueryResult:
    kind:         str   # "table" | "spatial_radius" | "spatial_knn" | "message" | "error"
    columns:      List[str]       = field(default_factory=list)
    rows:         List[Dict]      = field(default_factory=list)
    spatial_data: Optional[Dict]  = None    # payload para el canvas
    message:      str             = ""
    telemetry:    Dict[str, Any]  = field(default_factory=dict)


# Puente SQL: compila queries y ejecuta contra el engine de storage
class QueryBridge:

    # Inicializa engine y seed thread daemon para carga automática de datasets
    def __init__(self, data_dir: str):
        self._data_dir   = data_dir
        self._engine     = StorageEngine(data_dir)
        self._compiler   = QueryCompiler()
        self._seed_thread: Optional[_threading.Thread] = None
        init_concurrency(os.path.join(data_dir, "journal.log"))
        self._seed_thread = _threading.Thread(
            target=self._auto_seed, daemon=True, name="jupiter-seed"
        )
        self._seed_thread.start()

    def is_seeding(self) -> bool:
        return self._seed_thread is not None and self._seed_thread.is_alive()

    # Carga automática de datasets en workspace default_testing desde CSVs
    def _auto_seed(self) -> None:
        import csv as _csv
        if not self._data_dir.replace("\\", "/").rstrip("/").endswith("default_testing"):
            return
        tables = self.get_tables()

        if "Canciones" not in tables and os.path.exists(_CSV_CANCIONES):
            self._exec_sql_raw(
                "CREATE TABLE Canciones "
                "(ID INTEGER, Year INTEGER, Country VARCHAR(20), "
                "Artist VARCHAR(20), Song VARCHAR(30), Points INTEGER)"
            )
            with open(_CSV_CANCIONES, encoding="utf-8", errors="replace", newline="") as fh:
                for i, row in enumerate(_csv.DictReader(fh), start=1):
                    try:
                        pts = int(float(row.get("final_total_points", "") or 0))
                    except (ValueError, TypeError):
                        pts = 0
                    yr  = int(row.get("year", 0) or 0)
                    ctr = (row.get("country", "") or "")[:20].replace("'", "''")
                    art = (row.get("artist_name", "") or "")[:20].replace("'", "''")
                    sng = (row.get("song_name", "") or "")[:30].replace("'", "''")
                    try:
                        self._exec_sql_raw(
                            f"INSERT INTO Canciones VALUES ({i}, {yr}, '{ctr}', '{art}', '{sng}', {pts})"
                        )
                    except Exception:
                        pass

        if "Ciudades" not in tables and os.path.exists(_CSV_CIUDADES):
            self._exec_sql_raw(
                "CREATE TABLE Ciudades (ID INTEGER, Ciudad VARCHAR(20), Lat FLOAT, Lon FLOAT)"
                " INDEX (RTREE Lat)"
            )
            with open(_CSV_CIUDADES, encoding="utf-8", errors="replace", newline="") as fh:
                for i, row in enumerate(_csv.DictReader(fh), start=1):
                    try:
                        lat = float(row.get("Latitude", 0) or 0)
                        lon = float(row.get("Longitude", 0) or 0)
                    except (ValueError, TypeError):
                        continue
                    nombre = (row.get("City", "") or "")[:20].replace("'", "''")
                    try:
                        self._exec_sql_raw(
                            f"INSERT INTO Ciudades VALUES ({i}, '{nombre}', {lat}, {lon})"
                        )
                    except Exception:
                        pass

        if "Contests" not in tables and os.path.exists(_CSV_CONTESTS):
            self._exec_sql_raw(
                "CREATE TABLE Contests "
                "(ID INTEGER, Year INTEGER, Host VARCHAR(20), Date VARCHAR(12), "
                "SemiPaises INTEGER, FinalistPaises INTEGER, JuryVoters INTEGER, TeleVoters INTEGER)"
            )
            with open(_CSV_CONTESTS, encoding="utf-8", errors="replace", newline="") as fh:
                for i, row in enumerate(_csv.DictReader(fh), start=1):
                    try:
                        yr    = int(row.get("year", 0) or 0)
                        host  = (row.get("host", "") or "")[:20].replace("'", "''")
                        date  = (row.get("date", "") or "")[:12].replace("'", "''")
                        semi  = int(row.get("semi_countries", 0) or 0)
                        final = int(row.get("final_countries", 0) or 0)
                        jury  = int(row.get("jury_countries_voting_final", 0) or 0)
                        tele  = int(row.get("televote_countries_voting_final", 0) or 0)
                        self._exec_sql_raw(
                            f"INSERT INTO Contests VALUES "
                            f"({i}, {yr}, '{host}', '{date}', {semi}, {final}, {jury}, {tele})"
                        )
                    except Exception:
                        pass

        if "Paises" not in tables and os.path.exists(_CSV_PAISES):
            self._exec_sql_raw(
                "CREATE TABLE Paises (ID INTEGER, Pais VARCHAR(25), Region VARCHAR(20))"
            )
            with open(_CSV_PAISES, encoding="utf-8", errors="replace", newline="") as fh:
                for i, row in enumerate(_csv.DictReader(fh), start=1):
                    try:
                        pais   = (row.get("country", "") or "")[:25].replace("'", "''")
                        region = (row.get("region", "") or "")[:20].replace("'", "''")
                        self._exec_sql_raw(
                            f"INSERT INTO Paises VALUES ({i}, '{pais}', '{region}')"
                        )
                    except Exception:
                        pass

    # Reinicia engine con nuevo workspace
    def reinit(self, data_dir: str) -> None:
        self._engine._index_cache.clear()
        self._data_dir = data_dir
        self._engine   = StorageEngine(data_dir)
        init_concurrency(os.path.join(data_dir, "journal.log"))
        self._seed_thread = _threading.Thread(
            target=self._auto_seed, daemon=True, name="jupiter-seed"
        )
        self._seed_thread.start()

    # Ejecuta SQL directo sin resetear telemetría (uso interno)
    def _exec_sql_raw(self, sql: str) -> None:
        for node in self._compiler.compile(sql):
            self._engine.execute(node)

    # Retorna nombres de tablas registradas en el catálogo
    def get_tables(self) -> List[str]:
        return list(self._engine._catalog["tables"].keys())

    # Bulk-load desde CSV con soporte para RTree spatial index
    def massive_ingest(self, csv_path: str, table_name: str,
                       progress_cb=None,
                       spatial_index_cols: Optional[List[str]] = None) -> int:
        return self._engine.massive_ingest(
            csv_path, table_name, progress_cb, spatial_index_cols
        )

    # Punto de entrada público: ejecuta SQL y captura excepciones
    def execute(self, sql_text: str) -> QueryResult:
        sql = sql_text.strip()
        if not sql:
            return QueryResult("message", message="Empty query")
        Telemetry().reset()
        try:
            return self._exec_sql(sql)
        except Exception as exc:
            return QueryResult("error", message=str(exc),
                               telemetry=self._snapshot())

    # Compila y ejecuta SQL; detecta spatial queries para renderizar en canvas
    def _exec_sql(self, sql: str) -> QueryResult:
        nodes = self._compiler.compile(sql)
        last_rows: List[Dict] = []
        messages: List[str]   = []
        spatial_node: Optional[QueryNode] = None

        for node in nodes:
            result = self._engine.execute(node)
            if isinstance(result, list):
                last_rows = result
                if isinstance(node, QueryNode) and isinstance(node.filter, SpatialFilterExpr):
                    spatial_node = node
            elif isinstance(result, str):
                messages.append(result)

        tel = self._snapshot()

        # Si es query espacial, arma payload para dibujar en canvas
        if spatial_node is not None and last_rows is not None:
            return self._build_spatial_result(spatial_node, last_rows, tel)

        if last_rows:
            cols = list(last_rows[0].keys()) if last_rows else []
            return QueryResult("table", columns=cols, rows=last_rows, telemetry=tel)
        return QueryResult("message", message=_summarize_messages(messages), telemetry=tel)

    # Construye QueryResult con spatial_data para visualización en canvas
    def _build_spatial_result(
        self,
        node: QueryNode,
        hit_rows: List[Dict],
        tel: Dict,
    ) -> QueryResult:
        sf     = node.filter
        source = node.source
        meta   = self._engine._catalog["tables"].get(source, {})
        fields = meta.get("fields", [])

        # Usa columnas del query o fallback a rtree_col del catálogo
        if len(sf.columns) >= 2:
            rtree_col   = sf.columns[0].split(".")[-1]
            rtree_col_y = sf.columns[1].split(".")[-1]
        else:
            rtree_col   = meta.get("rtree_col",   "x")
            rtree_col_y = meta.get("rtree_col_y", "y")
        pk_field    = fields[0]["name"] if fields else "id"

        # Primer STRING del schema que no sea coordenada: etiqueta de punto
        lbl_field = next(
            (f["name"] for f in fields
             if f["type"] == "STRING"
             and f["name"] not in (rtree_col, rtree_col_y)),
            pk_field,
        )

        prefix = f"{source}."
        def bare(row: Dict) -> Dict:
            return {(k[len(prefix):] if k.startswith(prefix) else k): v
                    for k, v in row.items()}

        # Full scan para obtener todos los puntos (fondo del canvas)
        full_nodes = self._compiler.compile(f"SELECT * FROM {source}")
        full_rows  = list(self._engine.execute(full_nodes[0]))

        all_points = []
        for row in full_rows:
            b   = bare(row)
            x   = float(b.get(rtree_col, 0.0))
            y   = float(b.get(rtree_col_y, 0.0))
            rid = int(b.get(pk_field, 0))
            lbl = str(b.get(lbl_field, str(rid)))
            all_points.append((rid, x, y, lbl))

        hit_bare = [bare(r) for r in hit_rows]
        is_knn   = sf.k > 0

        if is_knn:
            results = []
            for b in hit_bare:
                x   = float(b.get(rtree_col, 0.0))
                y   = float(b.get(rtree_col_y, 0.0))
                rid = int(b.get(pk_field, 0))
                # Distancia euclidiana al punto de consulta
                d   = math.sqrt((x - sf.cx) ** 2 + (y - sf.cy) ** 2)
                results.append((x, y, rid, d))
            results.sort(key=lambda t: t[3])
            results = results[: sf.k]
            kind = "spatial_knn"
            spatial_data = {
                "query_type": "knn",
                "qx": sf.cx, "qy": sf.cy, "k": sf.k,
                "results":    results,
                "all_points": all_points,
            }
        else:
            results = [
                (float(b.get(rtree_col, 0.0)),
                 float(b.get(rtree_col_y, 0.0)),
                 int(b.get(pk_field, 0)))
                for b in hit_bare
            ]
            kind = "spatial_radius"
            spatial_data = {
                "query_type": "radius",
                "cx": sf.cx, "cy": sf.cy, "radius": sf.radius,
                "results":    results,
                "all_points": all_points,
            }

        cols     = [k[len(prefix):] if k.startswith(prefix) else k
                    for k in (hit_rows[0].keys() if hit_rows else [])]
        nice_rows = hit_bare

        return QueryResult(
            kind,
            columns=cols,
            rows=nice_rows,
            spatial_data=spatial_data,
            telemetry=tel,
        )

    # Snapshot actual de Telemetry (reads, writes, accesos, tiempo)
    def _snapshot(self) -> Dict[str, Any]:
        t = Telemetry()
        return {
            "reads":    t.pages_read,
            "writes":   t.pages_written,
            "accesses": t._disk_accesses,
            "ms":       t._total_ms,
        }

    def execute_sql(self, sql: str) -> Dict[str, Any]:
        result = self.execute(sql)
        d: Dict[str, Any] = {
            "kind":    result.kind,
            "columns": result.columns,
            "rows":    result.rows,
            "message": result.message,
        }
        if result.kind == "error":
            d["error"] = result.message
        return d

    def massive_ingest_csv(
        self,
        csv_path: str,
        table_name: str,
        progress_cb=None,
        spatial_index_cols: Optional[List[str]] = None,
    ) -> int:
        return self.massive_ingest(csv_path, table_name, progress_cb, spatial_index_cols)

    def wait_seed(self, timeout: float = 30.0) -> None:
        if self._seed_thread is not None and self._seed_thread.is_alive():
            self._seed_thread.join(timeout=timeout)


JupiterBridge = QueryBridge