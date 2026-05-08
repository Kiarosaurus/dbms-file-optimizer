from __future__ import annotations
import os
import struct
import heapq
import math
from typing import Any, Dict, List, Optional, Tuple

from core.storage import DiskController, PAGE_SIZE
from indexing.base_index import BaseIndex

META_FMT  = "!II"
META_SIZE = struct.calcsize(META_FMT)   # 8 bytes: root_id, total_nodes

NODE_INTERNAL = 0
NODE_LEAF     = 1

# Header del nodo: type(u8) | n_entries(u32)
NODE_HDR_FMT  = "!BI"
NODE_HDR_SIZE = struct.calcsize(NODE_HDR_FMT)   # 5 bytes

# Entrada interna: x_min(f) | y_min(f) | x_max(f) | y_max(f) | child_id(u32)
INT_ENTRY_FMT  = "!ffffI"
INT_ENTRY_SIZE = struct.calcsize(INT_ENTRY_FMT)  # 20 bytes

# Entrada de hoja: x(f) | y(f) | record_id(i64) — q para soportar BIGINT y negativos
LEAF_ENTRY_FMT  = "!ffq"
LEAF_ENTRY_SIZE = struct.calcsize(LEAF_ENTRY_FMT)  # 16 bytes

_DEFAULT_M_INT  = (PAGE_SIZE - NODE_HDR_SIZE) // INT_ENTRY_SIZE   # 204
_DEFAULT_M_LEAF = (PAGE_SIZE - NODE_HDR_SIZE) // LEAF_ENTRY_SIZE  # 255


class RTreeIndex(BaseIndex):

    # inicializa el r-tree desde disco o crea un nuevo archivo con root leaf vacío
    def __init__(
        self,
        file_path: str,
        max_entries: Optional[int] = None,  # override de M para demos
    ):
        self.file_path = file_path
        self.disk      = DiskController()

        self.M_int  = max_entries if max_entries is not None else _DEFAULT_M_INT
        self.M_leaf = max_entries if max_entries is not None else _DEFAULT_M_LEAF
        self.m_int  = max(2, self.M_int  // 2)
        self.m_leaf = max(2, self.M_leaf // 2)

        # Actualizados por spatial_query / knn_query
        self.nodes_entered: int = 0
        self.nodes_pruned:  int = 0

        if not os.path.exists(file_path):
            self._bootstrap()

    def add(self, point, record_id):
      x, y = point
      path = []
      leaf_id = self._choose_leaf(x, y, path)
      leaf_page = self._read_page(leaf_id)
      n = self._node_n(leaf_page)
      if n >= self.M_leaf:
          # TODO: implementar quadratic split en próximo commit
          raise RuntimeError(
              f"Leaf llena (M_leaf={self.M_leaf}). Quadratic split pendiente."
          )
      self._leaf_append(leaf_page, x, y, record_id, n)
      self._write_page(leaf_id, bytes(leaf_page))
      self._adjust_tree(leaf_id, -1, path)


    def spatial_query(self, cx, cy, radius):
        #TODO: implementar DFS con pruning en próximo commit
        raise NotImplementedError("spatial_query pendiente")

    def knn_query(self, qx, qy, k):
        # TODO: implementar best-first con min-heap en próximo commit
        raise NotImplementedError("knn_query pendiente")

    def search(self, key):
        raise NotImplementedError("use spatial_query() o knn_query()")

    def remove(self, key):
        # TODO: implementar full-scan leaf removal en próximo commit
        return 0

    def reorganize(self):
        pass

    def _split_leaf(self, entries):
        # TODO: quadratic split en próximo commit
        raise NotImplementedError

    def _split_internal(self, entries):
        # TODO: quadratic split en próximo commit
        raise NotImplementedError

    def _quadratic_split(self, mbrs, m):
        # TODO: implementar en próximo commit
        raise NotImplementedError

    def tree_stats(self):
        root_id, total = self._read_meta()
        return f"root_id={root_id}  total_nodes={total}  (stats parciales)"

     # ─── Helpers de MBR ───────────

    # calcula el MBR mínimo que envuelve todas las entradas del nodo
    def _compute_mbr(self, node_id: int) -> Tuple[float, float, float, float]:
        page = self._read_page(node_id)
        n    = self._node_n(page)
        if self._is_leaf(page):
            entries = self._read_leaf_entries(page)
            if not entries:
                return (0.0, 0.0, 0.0, 0.0)
            xs = [e[0] for e in entries]; ys = [e[1] for e in entries]
            return (min(xs), min(ys), max(xs), max(ys))
        else:
            entries = self._read_int_entries(page, n)
            if not entries:
                return (0.0, 0.0, 0.0, 0.0)
            return (min(e[0] for e in entries), min(e[1] for e in entries),
                    max(e[2] for e in entries), max(e[3] for e in entries))

       # ─── ChooseLeaf ──────────

    # elige el nodo hijo que requiere menor aumento de área — desciende hasta el leaf
    def _choose_leaf(
        self, x: float, y: float, path: List[Tuple[int, int]]
    ) -> int:
        root_id, _ = self._read_meta()
        current = root_id
        while True:
            page = self._read_page(current)
            if self._is_leaf(page):
                return current
            n = self._node_n(page)
            entries = self._read_int_entries(page, n)
            best_i    = 0
            best_enl  = float('inf')
            best_area = float('inf')
            for i, (x1, y1, x2, y2, _child) in enumerate(entries):
                a_before = _area(x1, y1, x2, y2)
                enl = _area(min(x1, x), min(y1, y),
                            max(x2, x), max(y2, y)) - a_before
                if enl < best_enl or (enl == best_enl and a_before < best_area):
                    best_i = i; best_enl = enl; best_area = a_before
            path.append((current, best_i))
            current = entries[best_i][4]

    # ─── Helpers a nivel de page ──────────

    # revisa el primer byte del page header para saber si es leaf o internal
    def _is_leaf(self, page: bytearray) -> bool:
        return page[0] == NODE_LEAF

    # extrae n_entries del node header
    def _node_n(self, page: bytearray) -> int:
        return struct.unpack_from(NODE_HDR_FMT, page, 0)[1]

    # deserializa todas las leaf entries del page como lista de (x, y, record_id)
    def _read_leaf_entries(
        self, page: bytearray
    ) -> List[Tuple[float, float, int]]:
        n = self._node_n(page)
        return [struct.unpack_from(LEAF_ENTRY_FMT, page,
                                   NODE_HDR_SIZE + i * LEAF_ENTRY_SIZE)
                for i in range(n)]

    # deserializa n internal entries como lista de (x1, y1, x2, y2, child_id)
    def _read_int_entries(
        self, page: bytearray, n: int
    ) -> List[Tuple[float, float, float, float, int]]:
        return [struct.unpack_from(INT_ENTRY_FMT, page,
                                   NODE_HDR_SIZE + i * INT_ENTRY_SIZE)
                for i in range(n)]

    # serializa un punto y actualiza el header del leaf page in-place
    def _leaf_append(
        self, page: bytearray, x: float, y: float, rid: int, n: int
    ) -> None:
        off = NODE_HDR_SIZE + n * LEAF_ENTRY_SIZE
        struct.pack_into(LEAF_ENTRY_FMT, page, off, x, y, rid)
        struct.pack_into(NODE_HDR_FMT,   page, 0,  NODE_LEAF, n + 1)

    # escribe una lista de leaf entries en una page nueva y la persiste a disco
    def _write_leaf_page(
        self, node_id: int, entries: List[Tuple[float, float, int]]
    ) -> None:
        page = bytearray(PAGE_SIZE)
        struct.pack_into(NODE_HDR_FMT, page, 0, NODE_LEAF, len(entries))
        for i, (x, y, rid) in enumerate(entries):
            struct.pack_into(LEAF_ENTRY_FMT, page,
                             NODE_HDR_SIZE + i * LEAF_ENTRY_SIZE, x, y, rid)
        self._write_page(node_id, bytes(page))

    # escribe una lista de internal entries con sus MBRs y child pointers
    def _write_int_page(
        self, node_id: int, entries: List[Tuple]
    ) -> None:
        page = bytearray(PAGE_SIZE)
        struct.pack_into(NODE_HDR_FMT, page, 0, NODE_INTERNAL, len(entries))
        for i, (x1, y1, x2, y2, cid) in enumerate(entries):
            struct.pack_into(INT_ENTRY_FMT, page,
                             NODE_HDR_SIZE + i * INT_ENTRY_SIZE,
                             x1, y1, x2, y2, cid)
        self._write_page(node_id, bytes(page))

     # ─── Metadata + I/O ────────

    # lee root_id y total_nodes del block 0
    def _read_meta(self) -> Tuple[int, int]:
        raw = self.disk.read_block(self.file_path, 0)
        return struct.unpack_from(META_FMT, raw, 0)

    # persiste root_id y total_nodes en el block 0
    def _write_meta(self, root_id: int, total_nodes: int) -> None:
        page = bytearray(PAGE_SIZE)
        struct.pack_into(META_FMT, page, 0, root_id, total_nodes)
        self.disk.write_block(self.file_path, 0, bytes(page))

    # incrementa total_nodes y retorna el nuevo node_id reservado
    def _alloc_node(self) -> int:
        root_id, total = self._read_meta()
        new_id = total + 1
        self._write_meta(root_id, new_id)
        return new_id

    # lee un block completo desde disco como bytearray mutable
    def _read_page(self, block_id: int) -> bytearray:
        return bytearray(self.disk.read_block(self.file_path, block_id))

    # escribe un block completo a disco
    def _write_page(self, block_id: int, data: bytes) -> None:
        self.disk.write_block(self.file_path, block_id, data)

    # crea metadata en block 0 y un root leaf vacío en block 1
    def _bootstrap(self) -> None:
        self._write_meta(root_id=1, total_nodes=1)
        page = bytearray(PAGE_SIZE)
        struct.pack_into(NODE_HDR_FMT, page, 0, NODE_LEAF, 0)
        self._write_page(1, bytes(page))


    # ─── Utilidades geométricas a nivel de módulo ─────────

# área del rectángulo — retorna 0 si las coordenadas están invertidas
def _area(x1: float, y1: float, x2: float, y2: float) -> float:
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


# distancia euclidiana mínima desde el punto (px, py) al MBR 
def _mindist(
    x1: float, y1: float, x2: float, y2: float,
    px: float, py: float,
) -> float:
    dx = max(0.0, max(x1 - px, px - x2))
    dy = max(0.0, max(y1 - py, py - y2))
    return math.sqrt(dx * dx + dy * dy)