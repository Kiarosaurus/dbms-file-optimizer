import os
import sys
import math
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from indexing.rtree import RTreeIndex


def _make_tree(tmpdir, max_entries=None):
    return RTreeIndex(
        file_path=os.path.join(tmpdir, "rtree.bin"),
        max_entries=max_entries,
    )

def test_bootstrap():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        root_id, total = tree._read_meta()
        assert root_id == 1, f"root_id esperado 1, obtenido {root_id}"
        assert total == 1, f"total_nodes esperado 1, obtenido {total}"
        page = tree._read_page(root_id)
        assert tree._is_leaf(page), "Root inicial debe ser leaf"
        assert tree._node_n(page) == 0, "Root inicial debe estar vacío"
    print("  [OK] test_bootstrap")


def test_insert_single_point():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        tree.add((1.0, 2.0), record_id=100)
        root_id, _ = tree._read_meta()
        page = tree._read_page(root_id)
        entries = tree._read_leaf_entries(page)
        assert len(entries) == 1
        assert abs(entries[0][0] - 1.0) < 1e-5
        assert abs(entries[0][1] - 2.0) < 1e-5
        assert entries[0][2] == 100
    print("  [OK] test_insert_single_point")


def test_insert_multiple_points_same_leaf():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td, max_entries=10)
        # insertar 9 puntos (< M_leaf=10, sin split)
        for i in range(9):
            tree.add((float(i), float(i)), record_id=i)
        root_id, _ = tree._read_meta()
        page = tree._read_page(root_id)
        assert tree._is_leaf(page)
        assert tree._node_n(page) == 9
    print("  [OK] test_insert_multiple_points_same_leaf")


def test_choose_leaf_root():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        path = []
        leaf_id = tree._choose_leaf(5.0, 5.0, path)
        root_id, _ = tree._read_meta()
        assert leaf_id == root_id, "Con solo root leaf, choose_leaf debe retornar root"
    print("  [OK] test_choose_leaf_root")



def test_split_on_overflow():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td, max_entries=4)
        for i in range(5):
            tree.add((float(i), float(i)), record_id=i)
        _, total = tree._read_meta()
        assert total > 1, f"Debe haber >1 nodo tras split, hay {total}"
    print("  [OK] test_split_on_overflow")


def test_spatial_query_radius():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        # cuadrícula 5x5 de puntos
        rid = 0
        for x in range(5):
            for y in range(5):
                tree.add((float(x), float(y)), record_id=rid)
                rid += 1
        # radio 0.6 desde (2.0, 2.0) 
        results = tree.spatial_query(2.0, 2.0, 0.6)
        assert len(results) >= 1, "Debe encontrar al menos el punto central"
        # radio 1.1 desde (2.0, 2.0) 
        results2 = tree.spatial_query(2.0, 2.0, 1.1)
        assert len(results2) >= 4, f"Radio 1.1 debe capturar >= 4 puntos, capturó {len(results2)}"
    print("  [OK] test_spatial_query_radius")


def test_spatial_query_empty_result():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in range(10):
            tree.add((float(i), float(i)), record_id=i)
        # buscar lejos de todos los puntos
        results = tree.spatial_query(1000.0, 1000.0, 0.5)
        assert len(results) == 0, f"Debe retornar vacío, retornó {len(results)}"
    print("  [OK] test_spatial_query_empty_result")


def test_knn_k3():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (5.0, 5.0), (10.0, 10.0)]
        for i, (x, y) in enumerate(points):
            tree.add((x, y), record_id=i)
        results = tree.knn_query(0.0, 0.0, k=3)
        assert len(results) == 3, f"KNN k=3 debe retornar 3 resultados, retornó {len(results)}"
        # el primero debe ser el punto (0,0) mismo (distancia 0)
        assert abs(results[0][3]) < 1e-5, f"Punto más cercano debe tener dist~0, tiene {results[0][3]}"
    print("  [OK] test_knn_k3")


def test_tree_stats():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td, max_entries=4)
        for i in range(20):
            tree.add((float(i), float(i)), record_id=i)
        stats = tree.tree_stats()
        assert "root_id" in stats
        assert "total_nodes" in stats
        assert "height" in stats
    print("  [OK] test_tree_stats")


def test_mindist_pruning():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td, max_entries=4)
        # insertar muchos puntos para crear un árbol de varios niveles
        for i in range(50):
            tree.add((float(i % 10), float(i // 10)), record_id=i)
        # query en zona concentrada
        tree.spatial_query(0.0, 0.0, 0.5)
        assert tree.nodes_pruned > 0, "El pruning por mindist debe haber podado al menos 1 nodo"
    print("  [OK] test_mindist_pruning")


if __name__ == "__main__":
    print("=== debug_rtree.py ===")
    test_bootstrap()
    test_insert_single_point()
    test_insert_multiple_points_same_leaf()
    test_choose_leaf_root()
    test_split_on_overflow()
    test_spatial_query_radius()
    test_spatial_query_empty_result()
    test_knn_k3()
    test_tree_stats()
    test_mindist_pruning()
    print("Todos los tests pasaron.")

