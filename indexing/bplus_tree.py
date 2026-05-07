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


    def search(self, key):
        # TODO: implementar descenso root->leaf (próximo commit)
        raise NotImplementedError("search no implementado aún")

    def add(self, record):
        # TODO: implementar insert con sorted placement (próximo commit)
        raise NotImplementedError("add no implementado aún")

    def remove(self, key):
        # TODO: implementar en commit posterior
        return 0

    def reorganize(self):
        pass  # B+ tree siempre ordenado; no requiere reorganización offline

    def range_search(self, start_key, end_key):
        # TODO: implementar recorrido de linked list de hojas (commit 09)
        raise NotImplementedError("range_search no implementado aún")

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


    # lee n_records y next_leaf del header de la hoja
    def _leaf_header(self, page: bytearray) -> Tuple[int, int]:
        _, n, nxt = struct.unpack_from(LEAF_HDR_FMT, page, 0)
        return n, nxt

    # escribe el header completo de la hoja incluyendo el puntero next_leaf
    def _set_leaf_header(self, page: bytearray, n_records: int, next_leaf: int) -> None:
        struct.pack_into(LEAF_HDR_FMT, page, 0, NODE_LEAF, n_records, next_leaf)

    
    # lee root_id y total_nodes del bloque 0 del archivo
    def _read_meta(self) -> Tuple[int, int]:
        raw = self.disk.read_block(self.file_path, 0)
        return struct.unpack_from(META_FMT, raw, 0)

    # escribe root_id y total_nodes en el bloque 0 del archivo
    def _write_meta(self, root_id: int, total_nodes: int) -> None:
        page = bytearray(PAGE_SIZE)
        struct.pack_into(META_FMT, page, 0, root_id, total_nodes)
        self.disk.write_block(self.file_path, 0, bytes(page))

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


    def _bootstrap(self) -> None:
        self._write_meta(root_id=1, total_nodes=1)
        root = bytearray(PAGE_SIZE)
        self._set_leaf_header(root, n_records=0, next_leaf=NO_NEXT)
        self._write_page(1, bytes(root))