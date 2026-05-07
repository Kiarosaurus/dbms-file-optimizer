from __future__ import annotations
import os
import struct
from typing import Any, Dict, List, Optional, Tuple

from core.storage import DiskController, PAGE_SIZE
from core.schema import RecordSerializer, FieldType
from indexing.base_index import BaseIndex

# El bloque 0 de cada archivo de árbol B+ almacena dos valores uint32.
META_FMT  = "!II"                       # root_id, total_nodes
META_SIZE = struct.calcsize(META_FMT)   # 8 bytes

NODE_INTERNAL = 0
NODE_LEAF     = 1

# Leaf header: type(u8) | n_records(u32) | next_leaf_id(u32)
LEAF_HDR_FMT  = "!BII"
LEAF_HDR_SIZE = struct.calcsize(LEAF_HDR_FMT)   # 9 bytes

# Internal header: type(u8) | n_keys(u32)
INT_HDR_FMT  = "!BI"
INT_HDR_SIZE = struct.calcsize(INT_HDR_FMT)     # 5 bytes

PTR_SIZE = 4   # uint32 block_id pointer
KEY_SIZE = 4   # int32 or float32 — both 4 bytes

NO_NEXT = 0xFFFFFFFF   

# ─── Geometría de slot de nodo interno ───────────────────────────────────────────────
#
#  Offset  Content
#  ------  -------
#  0       type  (1 byte)
#  1-4     n_keys (4 bytes)
#  5-8     P[0]  (4 bytes)           <- INT_HDR_SIZE
#  9-12    K[0]  (4 bytes)
#  13-16   P[1]  (4 bytes)
#  17-20   K[1]  (4 bytes)
#  21-24   P[2]  (4 bytes)
#  ...
#
#  P[i] at offset: INT_HDR_SIZE + i * (KEY_SIZE + PTR_SIZE)
#  K[i] at offset: INT_HDR_SIZE + PTR_SIZE + i * (KEY_SIZE + PTR_SIZE)


# árbol b+ con persistencia completa en disco via DiskController
class BPlusTreeIndex(BaseIndex):

    # inicializa el índice, calcula capacidades de leaf e internal según PAGE_SIZE
    def __init__(
        self,
        file_path: str,
        serializer: RecordSerializer,
        key_field: str,
    ):
        self.file_path   = file_path
        self.serializer  = serializer
        self.key_field   = key_field
        self.record_size = serializer.record_size
        self.disk        = DiskController()

        # Elige el formato de struct de clave según el tipo de campo
        kf = next(f for f in serializer.fields if f.name == key_field)
        if kf.field_type == FieldType.FLOAT:
            self._key_fmt  = "!f"
            self._key_is_str = False
        elif kf.field_type == FieldType.BIGINT:
            self._key_fmt  = "!q"
            self._key_is_str = False
        elif kf.field_type == FieldType.STRING:
            sz = kf.str_size or 20
            self._key_fmt  = f"!{sz}s"
            self._key_is_str = True
        else:  
            self._key_fmt  = "!i"
            self._key_is_str = False
        self._key_size = struct.calcsize(self._key_fmt)

        # Cuántos registros caben en una página hoja
        self.leaf_cap = (PAGE_SIZE - LEAF_HDR_SIZE) // self.record_size
        # Cuántas claves caben en una página interna
        self.internal_cap = (PAGE_SIZE - INT_HDR_SIZE - PTR_SIZE) // (self._key_size + PTR_SIZE)

        if not os.path.exists(file_path):
            self._bootstrap()

    # ─── API Pública ──────────

    # desciende root->leaf y escanea la hoja buscando la key
    def search(self, key: Any) -> Optional[Dict[str, Any]]:
        root_id, _ = self._read_meta()
        _, leaf_page = self._descend(key, root_id, path=None)
        return self._leaf_scan(leaf_page, key)

    # inserta el record en orden, hace split si la hoja está llena
    def add(self, record: Dict[str, Any]) -> None:
        root_id, _ = self._read_meta()
        key = record[self.key_field]
        path: List[Tuple[int, int]] = []
        leaf_id, leaf_page = self._descend(key, root_id, path)
        n_records, next_leaf = self._leaf_header(leaf_page)

        if n_records < self.leaf_cap:
            self._leaf_sorted_insert(leaf_page, record, n_records)
            self._set_leaf_header(leaf_page, n_records + 1, next_leaf)
            self._write_page(leaf_id, bytes(leaf_page))
        else:
            self._split_leaf(leaf_id, leaf_page, record, path)

    # elimina todos los records con la key dada; resuelve underflow con borrow-or-merge
    def remove(self, key: Any) -> int:
        root_id, _ = self._read_meta()
        path: List[Tuple[int, int]] = []
        leaf_id, leaf_page = self._descend(key, root_id, path)
        n, next_leaf = self._leaf_header(leaf_page)

        kept: List[Dict] = []
        removed = 0
        for i in range(n):
            rec = self._read_leaf_rec(leaf_page, i)
            if rec[self.key_field] == key:
                removed += 1
            else:
                kept.append(rec)

        if removed == 0:
            return 0

        self._write_leaf_records(leaf_id, kept, next_leaf)

        m_leaf = max(1, self.leaf_cap // 2)
        m_int  = max(1, self.internal_cap // 2)
        if len(kept) < m_leaf and path:
            did_merge = self._fix_leaf_underflow(leaf_id, kept, next_leaf, path, m_leaf)
            if did_merge:
                # Propaga underflow hacia arriba en nodos internos
                fix_path      = path
                node_to_check = fix_path[-1][0]   # padre de la hoja
                while True:
                    pg = self._read_page(node_to_check)
                    nk = self._int_n_keys(pg)
                    if nk >= m_int:
                        break
                    anc = fix_path[:-1]
                    if not anc:
                        # node_to_check es raíz
                        if nk == 0:
                            only = self._int_child(pg, 0)
                            _, tot = self._read_meta()
                            self._write_meta(only, tot)
                        break
                    par_id = anc[-1][0]
                    ci_par = anc[-1][1]
                    merged = self._fix_internal_underflow(
                        node_to_check, ci_par, par_id, m_int
                    )
                    if not merged:
                        break
                    fix_path      = anc
                    node_to_check = par_id

        return removed

    # no-op: el b+ tree mantiene orden en todo momento
    def reorganize(self) -> None:
        pass   # Árbol B+ siempre está ordenado; no requiere reorganización offline

    # recorre la linked list de hojas desde start_key hasta superar end_key
    def range_search(self, start_key: Any, end_key: Any) -> List[Dict[str, Any]]:
        root_id, _ = self._read_meta()
        _, page = self._descend(start_key, root_id, path=None)
        results: List[Dict[str, Any]] = []

        while True:
            n_records, next_leaf = self._leaf_header(page)
            past_end = False
            for i in range(n_records):
                rec = self._read_leaf_rec(page, i)
                k   = rec[self.key_field]
                if k > end_key:
                    past_end = True
                    break
                if k >= start_key:
                    results.append(rec)
            if past_end or next_leaf == NO_NEXT:
                break
            page = self._read_page(next_leaf)

        return results

    # ─── Recorrido del árbol ───────

    # desciende desde root hasta la hoja que contiene la key, acumula path si se pide
    def _descend(
        self,
        key: Any,
        start_id: int,
        path: Optional[List[Tuple[int, int]]],
    ) -> Tuple[int, bytearray]:
        current = start_id
        while True:
            page = self._read_page(current)
            if self._is_leaf(page):
                return current, page
            n_keys = self._int_n_keys(page)
            keys   = self._int_keys(page, n_keys)
            ci     = n_keys   # por defecto: hijo más a la derecha
            for i, k in enumerate(keys):
                if key < k:
                    ci = i
                    break
            if path is not None:
                # guarda (node_id, child_index) para usarlo en propagación de split
                path.append((current, ci))
            current = self._int_child(page, ci)

    # ─── Auxiliares de hoja ───────────

    # escanea la hoja linealmente buscando la primera coincidencia de key
    def _leaf_scan(self, page: bytearray, key: Any) -> Optional[Dict[str, Any]]:
        n_records, _ = self._leaf_header(page)
        for i in range(n_records):
            rec = self._read_leaf_rec(page, i)
            if rec[self.key_field] == key:
                return rec
        return None

    # inserta el record en la posición correcta desplazando registros a la derecha
    def _leaf_sorted_insert(
        self, page: bytearray, record: Dict[str, Any], n_records: int
    ) -> None:
        key = record[self.key_field]
        pos = n_records
        for i in range(n_records):
            if key < self._read_leaf_rec(page, i)[self.key_field]:
                pos = i
                break
        # desplaza registros a la derecha para abrir un slot en pos
        for j in range(n_records, pos, -1):
            src = LEAF_HDR_SIZE + (j - 1) * self.record_size
            dst = LEAF_HDR_SIZE + j * self.record_size
            page[dst : dst + self.record_size] = page[src : src + self.record_size]
        off = LEAF_HDR_SIZE + pos * self.record_size
        page[off : off + self.record_size] = self.serializer.serialize(record)

    # lee el record en el slot idx usando byte offset fijo desde LEAF_HDR_SIZE
    def _read_leaf_rec(self, page: bytearray, idx: int) -> Dict[str, Any]:
        off = LEAF_HDR_SIZE + idx * self.record_size
        return self.serializer.deserialize(bytes(page[off : off + self.record_size]))

    # lee n_records y next_leaf del header de la hoja
    def _leaf_header(self, page: bytearray) -> Tuple[int, int]:
        _, n, nxt = struct.unpack_from(LEAF_HDR_FMT, page, 0)
        return n, nxt

    # escribe el header completo de la hoja incluyendo el puntero next_leaf
    def _set_leaf_header(self, page: bytearray, n_records: int, next_leaf: int) -> None:
        struct.pack_into(LEAF_HDR_FMT, page, 0, NODE_LEAF, n_records, next_leaf)

    # serializa records en una nueva página y la escribe — helper para remove y merge
    def _write_leaf_records(
        self, leaf_id: int, records: List[Dict], next_leaf: int
    ) -> None:
        page = bytearray(PAGE_SIZE)
        self._set_leaf_header(page, len(records), next_leaf)
        for i, rec in enumerate(records):
            off = LEAF_HDR_SIZE + i * self.record_size
            page[off: off + self.record_size] = self.serializer.serialize(rec)
        self._write_page(leaf_id, bytes(page))

    # resuelve underflow de hoja: borrow del hermano (False) o merge (True)
    def _fix_leaf_underflow(
        self,
        leaf_id: int,
        leaf_recs: List[Dict],
        leaf_next: int,
        path: List[Tuple[int, int]],
        m_leaf: int,
    ) -> bool:
        parent_id, ci = path[-1]
        parent_page   = self._read_page(parent_id)
        n_keys        = self._int_n_keys(parent_page)
        keys          = self._int_keys(parent_page, n_keys)
        children      = [self._int_child(parent_page, i) for i in range(n_keys + 1)]

        if ci > 0:
            # ── Hermano izquierdo ─────
            sib_id   = children[ci - 1]
            sib_page = self._read_page(sib_id)
            sib_n, sib_next = self._leaf_header(sib_page)
            sib_recs = [self._read_leaf_rec(sib_page, i) for i in range(sib_n)]

            if sib_n > m_leaf:
                # Toma prestado el último registro del hermano izquierdo
                borrowed      = sib_recs[-1]
                self._write_leaf_records(sib_id,  sib_recs[:-1],         sib_next)
                self._write_leaf_records(leaf_id, [borrowed] + leaf_recs, leaf_next)
                keys[ci - 1] = borrowed[self.key_field]
                self._pack_internal(parent_page, keys, children)
                self._write_page(parent_id, bytes(parent_page))
                return False

            # Combina: el izquierdo absorbe la hoja actual
            self._write_leaf_records(sib_id, sib_recs + leaf_recs, leaf_next)
            del keys[ci - 1]
            del children[ci]

        elif ci < n_keys:
            # ── Hermano derecho ────
            sib_id   = children[ci + 1]
            sib_page = self._read_page(sib_id)
            sib_n, sib_next = self._leaf_header(sib_page)
            sib_recs = [self._read_leaf_rec(sib_page, i) for i in range(sib_n)]

            if sib_n > m_leaf:
                # Toma prestado el primer registro del hermano derecho
                borrowed      = sib_recs[0]
                new_sib       = sib_recs[1:]
                self._write_leaf_records(leaf_id, leaf_recs + [borrowed], leaf_next)
                self._write_leaf_records(sib_id,  new_sib,               sib_next)
                keys[ci] = (new_sib[0] if new_sib else borrowed)[self.key_field]
                self._pack_internal(parent_page, keys, children)
                self._write_page(parent_id, bytes(parent_page))
                return False

            # Combina: la hoja actual absorbe al hermano derecho
            self._write_leaf_records(leaf_id, leaf_recs + sib_recs, sib_next)
            del keys[ci]
            del children[ci + 1]

        else:
            return False  # la hoja es el único hijo — no hay nada que hacer

        # ── Actualiza padre después de combinar; colapso de raíz manejado por llamador ─────────
        new_page = bytearray(PAGE_SIZE)
        self._pack_internal(new_page, keys, children)
        self._write_page(parent_id, bytes(new_page))
        return True

    # resuelve underflow de nodo interno: borrow del hermano (False) o merge (True)
    def _fix_internal_underflow(
        self, node_id: int, ci: int, parent_id: int, m_int: int
    ) -> bool:
        parent_page = self._read_page(parent_id)
        n_keys      = self._int_n_keys(parent_page)
        keys        = self._int_keys(parent_page, n_keys)
        children    = [self._int_child(parent_page, i) for i in range(n_keys + 1)]
        page        = self._read_page(node_id)
        n_node      = self._int_n_keys(page)
        nk          = self._int_keys(page, n_node)
        nc          = [self._int_child(page, i) for i in range(n_node + 1)]

        if ci > 0:
            sib_id   = children[ci - 1]
            sib_page = self._read_page(sib_id)
            sib_n    = self._int_n_keys(sib_page)
            sib_keys = self._int_keys(sib_page, sib_n)
            sib_ch   = [self._int_child(sib_page, i) for i in range(sib_n + 1)]
            if sib_n > m_int:
                # Rota a la derecha: baja separador, sube la clave más a la derecha del hermano
                pulled   = keys[ci - 1]
                new_page = bytearray(PAGE_SIZE)
                self._pack_internal(new_page, [pulled] + nk, [sib_ch[-1]] + nc)
                self._write_page(node_id, bytes(new_page))
                new_sib  = bytearray(PAGE_SIZE)
                self._pack_internal(new_sib, sib_keys[:-1], sib_ch[:-1])
                self._write_page(sib_id, bytes(new_sib))
                keys[ci - 1] = sib_keys[-1]
                self._pack_internal(parent_page, keys, children)
                self._write_page(parent_id, bytes(parent_page))
                return False
            # Combina: hermano izquierdo absorbe node_id (baja separador)
            pulled      = keys[ci - 1]
            merged_keys = sib_keys + [pulled] + nk
            merged_ch   = sib_ch + nc
            new_sib     = bytearray(PAGE_SIZE)
            self._pack_internal(new_sib, merged_keys, merged_ch)
            self._write_page(sib_id, bytes(new_sib))
            del keys[ci - 1]
            del children[ci]

        elif ci < n_keys:
            sib_id   = children[ci + 1]
            sib_page = self._read_page(sib_id)
            sib_n    = self._int_n_keys(sib_page)
            sib_keys = self._int_keys(sib_page, sib_n)
            sib_ch   = [self._int_child(sib_page, i) for i in range(sib_n + 1)]
            if sib_n > m_int:
                # Rota a la izquierda: baja separador, sube la clave más a la izquierda del hermano
                pulled   = keys[ci]
                new_page = bytearray(PAGE_SIZE)
                self._pack_internal(new_page, nk + [pulled], nc + [sib_ch[0]])
                self._write_page(node_id, bytes(new_page))
                new_sib  = bytearray(PAGE_SIZE)
                self._pack_internal(new_sib, sib_keys[1:], sib_ch[1:])
                self._write_page(sib_id, bytes(new_sib))
                keys[ci] = sib_keys[0]
                self._pack_internal(parent_page, keys, children)
                self._write_page(parent_id, bytes(parent_page))
                return False
            # Combina: node_id absorbe hermano derecho 
            pulled      = keys[ci]
            merged_keys = nk + [pulled] + sib_keys
            merged_ch   = nc + sib_ch
            new_page    = bytearray(PAGE_SIZE)
            self._pack_internal(new_page, merged_keys, merged_ch)
            self._write_page(node_id, bytes(new_page))
            del keys[ci]
            del children[ci + 1]

        else:
            return False

        new_parent = bytearray(PAGE_SIZE)
        self._pack_internal(new_parent, keys, children)
        self._write_page(parent_id, bytes(new_parent))
        return True

    # ─── División de hoja ───────────

    # divide la hoja llena: mitad izquierda, mitad derecha, promueve key al parent
    def _split_leaf(
        self,
        leaf_id: int,
        leaf_page: bytearray,
        new_record: Dict[str, Any],
        path: List[Tuple[int, int]],
    ) -> None:
        all_recs: List[Dict] = [
            self._read_leaf_rec(leaf_page, i) for i in range(self.leaf_cap)
        ]
        all_recs.append(new_record)
        all_recs.sort(key=lambda r: r[self.key_field])

        mid         = len(all_recs) // 2
        _, old_next = self._leaf_header(leaf_page)
        right_id    = self._alloc_node()

        left_page  = bytearray(PAGE_SIZE)
        right_page = bytearray(PAGE_SIZE)
        # hoja izquierda apunta a la nueva hoja derecha; derecha mantiene old_next
        self._set_leaf_header(left_page,  mid,                  right_id)
        self._set_leaf_header(right_page, len(all_recs) - mid,  old_next)

        for i, rec in enumerate(all_recs[:mid]):
            off = LEAF_HDR_SIZE + i * self.record_size
            left_page[off : off + self.record_size] = self.serializer.serialize(rec)
        for i, rec in enumerate(all_recs[mid:]):
            off = LEAF_HDR_SIZE + i * self.record_size
            right_page[off : off + self.record_size] = self.serializer.serialize(rec)

        self._write_page(leaf_id,  bytes(left_page))
        self._write_page(right_id, bytes(right_page))

        # primera key de la mitad derecha sube al parent
        promoted = all_recs[mid][self.key_field]
        self._push_up(path, leaf_id, promoted, right_id)

    # ─── División interna / Propagación de clave ────────────────────

    # propaga la key promovida hacia el parent, recursivo si el parent también llena
    def _push_up(
        self,
        path: List[Tuple[int, int]],
        left_id: int,
        promoted_key: Any,
        right_id: int,
    ) -> None:
        if not path:
            # path vacío: la key promovida se convierte en la nueva raíz
            new_root_id = self._alloc_node()
            page = bytearray(PAGE_SIZE)
            self._pack_internal(page, [promoted_key], [left_id, right_id])
            self._write_page(new_root_id, bytes(page))
            _, total = self._read_meta()
            self._write_meta(new_root_id, total)
            return

        parent_id, ci = path.pop()
        parent_page   = self._read_page(parent_id)
        n_keys        = self._int_n_keys(parent_page)

        old_keys = self._int_keys(parent_page, n_keys)
        old_ch   = [self._int_child(parent_page, i) for i in range(n_keys + 1)]

        # inserta la key promovida y el puntero derecho en la posición ci
        new_keys = old_keys[:ci] + [promoted_key] + old_keys[ci:]
        new_ch   = old_ch[:ci + 1] + [right_id] + old_ch[ci + 1:]

        if n_keys < self.internal_cap:
            self._pack_internal(parent_page, new_keys, new_ch)
            self._write_page(parent_id, bytes(parent_page))
        else:
            # nodo interno también lleno: split y burbujea la key del medio
            mid         = len(new_keys) // 2
            bubble_key  = new_keys[mid]
            left_keys   = new_keys[:mid]
            right_keys  = new_keys[mid + 1:]
            left_ch_  = new_ch[:mid + 1]
            right_ch_ = new_ch[mid + 1:]

            right_node_id = self._alloc_node()
            left_page     = bytearray(PAGE_SIZE)
            right_page    = bytearray(PAGE_SIZE)
            self._pack_internal(left_page,  left_keys,  left_ch_)
            self._pack_internal(right_page, right_keys, right_ch_)
            self._write_page(parent_id,     bytes(left_page))
            self._write_page(right_node_id, bytes(right_page))

            self._push_up(path, parent_id, bubble_key, right_node_id)

    # ─── Internal node binary packing ───────────

    # empaqueta header + punteros/keys de un nodo interno en la page: P[0] K[0] P[1] K[1] ...
    def _pack_internal(
        self, page: bytearray, keys: List[Any], children: List[int]
    ) -> None:
        struct.pack_into(INT_HDR_FMT, page, 0, NODE_INTERNAL, len(keys))
        off = INT_HDR_SIZE
        struct.pack_into("!I", page, off, children[0])
        off += PTR_SIZE
        for i, k in enumerate(keys):
            if self._key_is_str:
                raw_k = (k.encode("utf-8") if isinstance(k, str) else k)[: self._key_size]
                page[off : off + self._key_size] = raw_k.ljust(self._key_size, b"\x00")
            else:
                struct.pack_into(self._key_fmt, page, off, k)
            off += self._key_size
            struct.pack_into("!I", page, off, children[i + 1])
            off += PTR_SIZE

    # retorna true si el byte de tipo en la page es NODE_LEAF
    def _is_leaf(self, page: bytearray) -> bool:
        return page[0] == NODE_LEAF

    # lee n_keys del header de un nodo interno
    def _int_n_keys(self, page: bytearray) -> int:
        return struct.unpack_from(INT_HDR_FMT, page, 0)[1]

    # extrae la lista de keys de un nodo interno usando el layout P K P K ...
    def _int_keys(self, page: bytearray, n_keys: int) -> List[Any]:
        base = INT_HDR_SIZE + PTR_SIZE        # offset of K[0]
        step = self._key_size + PTR_SIZE      # bytes per (K, P) pair after P[0]
        keys = [struct.unpack_from(self._key_fmt, page, base + i * step)[0]
                for i in range(n_keys)]
        if self._key_is_str:
            keys = [k.rstrip(b"\x00").decode("utf-8", errors="replace") for k in keys]
        return keys

    # lee el block_id del child ci: P[i] arranca en INT_HDR_SIZE + i * (key_size + PTR_SIZE)
    def _int_child(self, page: bytearray, ci: int) -> int:
        off = INT_HDR_SIZE + ci * (self._key_size + PTR_SIZE)
        return struct.unpack_from("!I", page, off)[0]

    # ─── Metadata + page I/O ───────

    # lee root_id y total_nodes del bloque 0 del archivo
    def _read_meta(self) -> Tuple[int, int]:
        raw = self.disk.read_block(self.file_path, 0)
        return struct.unpack_from(META_FMT, raw, 0)

    # escribe root_id y total_nodes en el bloque 0 del archivo
    def _write_meta(self, root_id: int, total_nodes: int) -> None:
        page = bytearray(PAGE_SIZE)
        struct.pack_into(META_FMT, page, 0, root_id, total_nodes)
        self.disk.write_block(self.file_path, 0, bytes(page))

    # incrementa total_nodes en metadata y retorna el nuevo block_id
    def _alloc_node(self) -> int:
        root_id, total = self._read_meta()
        new_id = total + 1
        self._write_meta(root_id, new_id)
        return new_id

    # lee una page completa como bytearray mutable
    def _read_page(self, block_id: int) -> bytearray:
        return bytearray(self.disk.read_block(self.file_path, block_id))

    # escribe una page en el bloque indicado
    def _write_page(self, block_id: int, data: bytes) -> None:
        self.disk.write_block(self.file_path, block_id, data)

    # crea el bloque 0 de metadata y la hoja raíz vacía en bloque 1
    def _bootstrap(self) -> None:
        self._write_meta(root_id=1, total_nodes=1)
        root = bytearray(PAGE_SIZE)
        self._set_leaf_header(root, n_records=0, next_leaf=NO_NEXT)
        self._write_page(1, bytes(root))
