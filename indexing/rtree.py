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

    # --- API pública ------------

    # inserta un punto 2d usando descenso choose_leaf y quadratic split si hay overflow
    def add(self, point: Tuple[float, float], record_id: int) -> None: 
        x, y = point
        path: List[Tuple[int, int]] = []   # (node_id, entry_index elegido)
        leaf_id = self._choose_leaf(x, y, path)

        leaf_page = self._read_page(leaf_id)
        n = self._node_n(leaf_page)

        if n < self.M_leaf:
            self._leaf_append(leaf_page, x, y, record_id, n)
            self._write_page(leaf_id, bytes(leaf_page))
            self._adjust_tree(leaf_id, -1, path)
        else:
            entries = self._read_leaf_entries(leaf_page)
            entries.append((x, y, record_id))
            left_ents, right_ents = self._split_leaf(entries)
            new_leaf_id = self._alloc_node()
            self._write_leaf_page(leaf_id,     left_ents)
            self._write_leaf_page(new_leaf_id, right_ents)
            self._adjust_tree(leaf_id, new_leaf_id, path)

    # busca todos los puntos dentro del radio euclidiano 
    def spatial_query(
        self, cx: float, cy: float, radius: float
    ) -> List[Tuple[float, float, int]]:
        self.nodes_entered = 0
        self.nodes_pruned  = 0
        results: List[Tuple[float, float, int]] = []
        root_id, _ = self._read_meta()
        self._spatial_dfs(root_id, cx, cy, radius, results)
        return results

    # knn best-first con min-heap ordenado por mindist 
    def knn_query(
        self, qx: float, qy: float, k: int
    ) -> List[Tuple[float, float, int, float]]:
        self.nodes_entered = 0
        self.nodes_pruned  = 0
        root_id, _ = self._read_meta()

        counter = 0
        heap: List[Tuple[float, int, tuple]] = []
        # counter desempata en el heap cuando dos prioridades son iguales
        heapq.heappush(heap, (0.0, counter, ('node', root_id)))

        results: List[Tuple[float, float, int, float]] = []
        while heap and len(results) < k:
            pri, _, item = heapq.heappop(heap)
            kind = item[0]

            if kind == 'point':
                results.append((item[1], item[2], item[3], pri))
                continue

            node_id = item[1]
            page = self._read_page(node_id)
            self.nodes_entered += 1
            n = self._node_n(page)

            if self._is_leaf(page):
                for (ex, ey, rid) in self._read_leaf_entries(page):
                    d = math.sqrt((ex - qx) ** 2 + (ey - qy) ** 2)
                    counter += 1
                    heapq.heappush(heap, (d, counter, ('point', ex, ey, rid)))
            else:
                for (x1, y1, x2, y2, child_id) in self._read_int_entries(page, n):
                    md = _mindist(x1, y1, x2, y2, qx, qy)
                    counter += 1
                    heapq.heappush(heap, (md, counter, ('node', child_id)))

        return results

    def search(self, key: Any) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("use spatial_query() or knn_query()")

    # elimina entradas con record_id == key; actualiza MBRs y condensa underflows
    def remove(self, key: Any) -> int:
        root_id, _ = self._read_meta()
        removed = 0

        # DFS desde root — solo visita hojas alcanzables, evitando páginas huérfanas
        modified_leaves: List[int] = []
        stack: List[int] = [root_id]
        while stack:
            node_id = stack.pop()
            page = self._read_page(node_id)
            if self._is_leaf(page):
                entries = self._read_leaf_entries(page)
                kept = [(x, y, rid) for x, y, rid in entries if rid != key]
                if len(kept) < len(entries):
                    removed += len(entries) - len(kept)
                    self._write_leaf_page(node_id, kept)
                    modified_leaves.append(node_id)
            else:
                n = self._node_n(page)
                for *_, child_id in self._read_int_entries(page, n):
                    stack.append(child_id)

        # condensa el árbol desde cada hoja modificada
        for leaf_id in modified_leaves:
            self._condense_tree(leaf_id)

        return removed

    def reorganize(self) -> None:
        pass   

    # --- Helpers de eliminación ------------

    # DFS root->target acumulando (parent_id, child_index); retorna True si lo encuentra
    def _find_path(
        self,
        node_id: int,
        target_id: int,
        path: List[Tuple[int, int]],
    ) -> bool:
        if node_id == target_id:
            return True
        page = self._read_page(node_id)
        if self._is_leaf(page):
            return False
        n = self._node_n(page)
        for i, (*_, child_id) in enumerate(self._read_int_entries(page, n)):
            path.append((node_id, i))
            if self._find_path(child_id, target_id, path):
                return True
            path.pop()
        return False

    # recolecta recursivamente todas las leaf entries del subtree en node_id
    def _collect_leaf_entries(
        self, node_id: int
    ) -> List[Tuple[float, float, int]]:
        page = self._read_page(node_id)
        if self._is_leaf(page):
            return list(self._read_leaf_entries(page))
        n = self._node_n(page)
        result: List[Tuple[float, float, int]] = []
        for *_, child_id in self._read_int_entries(page, n):
            result.extend(self._collect_leaf_entries(child_id))
        return result

    # sube desde start_id hasta root: actualiza MBRs y maneja underflow por reinserción
    def _condense_tree(self, start_id: int) -> None:
        root_id, _ = self._read_meta()
        if start_id == root_id:
            return  # la hoja raíz puede quedar vacía

        path: List[Tuple[int, int]] = []
        if not self._find_path(root_id, start_id, path):
            return  # nodo ya desconectado del árbol

        reinsert: List[Tuple[float, float, int]] = []
        current = start_id

        while path:
            parent_id, ci = path.pop()
            cur_page = self._read_page(current)
            n        = self._node_n(cur_page)
            m        = self.m_leaf if self._is_leaf(cur_page) else self.m_int

            parent_page = self._read_page(parent_id)
            n_p         = self._node_n(parent_page)
            p_entries   = list(self._read_int_entries(parent_page, n_p))

            if n < m:
                # underflow: saca el nodo del parent y acumula sus entradas para reinsertar
                reinsert.extend(self._collect_leaf_entries(current))
                p_entries.pop(ci)
                self._write_int_page(parent_id, p_entries)
            else:
                # sin underflow: actualiza el MBR de current en el parent
                new_mbr = self._compute_mbr(current)
                p_entries[ci] = (*new_mbr, p_entries[ci][4])
                self._write_int_page(parent_id, p_entries)

            current = parent_id

        # acorta el árbol si la raíz quedó con un solo hijo
        root_id, total = self._read_meta()
        root_page = self._read_page(root_id)
        if not self._is_leaf(root_page) and self._node_n(root_page) == 1:
            only_child = self._read_int_entries(root_page, 1)[0][4]
            self._write_meta(only_child, total)

        # reinserta entradas de nodos eliminados por underflow
        for x, y, rid in reinsert:
            self.add((x, y), rid)

    # --- ChooseLeaf ----------

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

    # --- AdjustTree ------------
    # sube por el path actualizando MBRs y propagando splits hasta la raíz
    def _adjust_tree(
        self,
        node_id: int,
        split_id: int,           # -1 si no ocurrió split en este nivel
        path: List[Tuple[int, int]],
    ) -> None:
        current = node_id
        sibling = split_id

        while path:
            parent_id, ci = path.pop()
            parent_page   = self._read_page(parent_id)
            n_p           = self._node_n(parent_page)
            p_entries     = self._read_int_entries(parent_page, n_p)

            # recalcula el MBR del nodo actual porque su contenido cambió
            cur_mbr = self._compute_mbr(current)
            p_entries[ci] = (*cur_mbr, p_entries[ci][4])

            if sibling != -1:
                sib_mbr   = self._compute_mbr(sibling)
                new_entry = (*sib_mbr, sibling)
                if n_p < self.M_int:
                    p_entries.append(new_entry)
                    self._write_int_page(parent_id, p_entries)
                    sibling = -1
                else:
                    # overflow en nodo interno 
                    p_entries.append(new_entry)
                    left_e, right_e = self._split_internal(p_entries)
                    new_int_id = self._alloc_node()
                    self._write_int_page(parent_id,  left_e)
                    self._write_int_page(new_int_id, right_e)
                    sibling = new_int_id
            else:
                self._write_int_page(parent_id, p_entries)

            current = parent_id

        if sibling != -1:
            # la raíz se splitteó
            new_root_id = self._alloc_node()
            cur_mbr = self._compute_mbr(current)
            sib_mbr = self._compute_mbr(sibling)
            self._write_int_page(new_root_id,
                                 [(*cur_mbr, current), (*sib_mbr, sibling)])
            _, total = self._read_meta()
            self._write_meta(new_root_id, total)

    # --- Spatial DFS -----------

    # dfs recursivo que poda subtrees cuyo MBR mindist supera el radio
    def _spatial_dfs(
        self,
        node_id: int,
        cx: float, cy: float, radius: float,
        results: List[Tuple[float, float, int]],
    ) -> None:
        page = self._read_page(node_id)
        self.nodes_entered += 1
        n = self._node_n(page)

        if self._is_leaf(page):
            for (x, y, rid) in self._read_leaf_entries(page):
                if math.sqrt((x - cx) ** 2 + (y - cy) ** 2) <= radius:
                    results.append((x, y, rid))
        else:
            for (x1, y1, x2, y2, child_id) in self._read_int_entries(page, n):
                if _mindist(x1, y1, x2, y2, cx, cy) <= radius:
                    self._spatial_dfs(child_id, cx, cy, radius, results)
                else:
                    self.nodes_pruned += 1

    # --- Quadratic Split ----

    # wrapper que convierte leaf entries a MBRs puntuales y llama al split genérico
    def _split_leaf(
        self, entries: List[Tuple[float, float, int]]
    ) -> Tuple[List, List]:
        mbrs = [(x, y, x, y) for (x, y, _) in entries]
        g1, g2 = self._quadratic_split(mbrs, self.m_leaf)
        return [entries[i] for i in g1], [entries[i] for i in g2]

    # wrapper para internal entries 
    def _split_internal(
        self, entries: List[Tuple]
    ) -> Tuple[List, List]:
        mbrs = [(e[0], e[1], e[2], e[3]) for e in entries]
        g1, g2 = self._quadratic_split(mbrs, self.m_int)
        return [entries[i] for i in g1], [entries[i] for i in g2]

    # quadratic split: separa las entradas del nodo lleno en dos grupos balanceados
    def _quadratic_split(
        self, mbrs: List[Tuple[float, float, float, float]], m: int
    ) -> Tuple[List[int], List[int]]:
        n = len(mbrs)

        # pickseeds: busca el par con mayor waste, es decir, MBR combinado 
        s1, s2, max_waste = 0, 1, float('-inf')
        for i in range(n):
            for j in range(i + 1, n):
                x1 = min(mbrs[i][0], mbrs[j][0]); y1 = min(mbrs[i][1], mbrs[j][1])
                x2 = max(mbrs[i][2], mbrs[j][2]); y2 = max(mbrs[i][3], mbrs[j][3])
                w = _area(x1, y1, x2, y2) - _area(*mbrs[i]) - _area(*mbrs[j])
                if w > max_waste:
                    max_waste = w; s1 = i; s2 = j

        g1 = [s1]; g2 = [s2]
        mbr1 = list(mbrs[s1]); mbr2 = list(mbrs[s2])
        remaining = [i for i in range(n) if i not in (s1, s2)]

        while remaining:
            total = len(remaining)
            if len(g1) + total == m:
                g1.extend(remaining); break
            if len(g2) + total == m:
                g2.extend(remaining); break

            best_idx  = 0
            best_diff = float('-inf')
            for idx, i in enumerate(remaining):
                e1 = (_area(min(mbr1[0], mbrs[i][0]), min(mbr1[1], mbrs[i][1]),
                            max(mbr1[2], mbrs[i][2]), max(mbr1[3], mbrs[i][3]))
                      - _area(*mbr1))
                e2 = (_area(min(mbr2[0], mbrs[i][0]), min(mbr2[1], mbrs[i][1]),
                            max(mbr2[2], mbrs[i][2]), max(mbr2[3], mbrs[i][3]))
                      - _area(*mbr2))
                diff = abs(e1 - e2)
                if diff > best_diff:
                    best_diff = diff; best_idx = idx

            chosen = remaining.pop(best_idx)
            e1 = (_area(min(mbr1[0], mbrs[chosen][0]), min(mbr1[1], mbrs[chosen][1]),
                        max(mbr1[2], mbrs[chosen][2]), max(mbr1[3], mbrs[chosen][3]))
                  - _area(*mbr1))
            e2 = (_area(min(mbr2[0], mbrs[chosen][0]), min(mbr2[1], mbrs[chosen][1]),
                        max(mbr2[2], mbrs[chosen][2]), max(mbr2[3], mbrs[chosen][3]))
                  - _area(*mbr2))
            if e1 < e2 or (e1 == e2 and _area(*mbr1) <= _area(*mbr2)):
                g1.append(chosen)
                mbr1 = [min(mbr1[0], mbrs[chosen][0]), min(mbr1[1], mbrs[chosen][1]),
                        max(mbr1[2], mbrs[chosen][2]), max(mbr1[3], mbrs[chosen][3])]
            else:
                g2.append(chosen)
                mbr2 = [min(mbr2[0], mbrs[chosen][0]), min(mbr2[1], mbrs[chosen][1]),
                        max(mbr2[2], mbrs[chosen][2]), max(mbr2[3], mbrs[chosen][3])]

        return g1, g2

    # --- Helpers de MBR -----------

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

    # --- Helpers a nivel de page ----------

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

    # --- Metadata + I/O --------

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

    # --- Helpers de debug ----------

    # retorna stats del árbol: root, total nodes, height y capacidades M
    def tree_stats(self) -> str:
        root_id, total = self._read_meta()
        height = 0
        node_id = root_id
        while True:
            page = self._read_page(node_id)
            height += 1
            if self._is_leaf(page):
                break
            n = self._node_n(page)
            entries = self._read_int_entries(page, n)
            node_id = entries[0][4]   # sigue el hijo más a la izquierda
        return (f"root_id={root_id}  total_nodes={total}  "
                f"height={height}  "
                f"M_leaf={self.M_leaf}  M_int={self.M_int}")


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
