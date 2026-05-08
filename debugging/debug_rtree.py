
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



if __name__ == "__main__":
    test_bootstrap()
    test_insert_single_point()
    test_insert_multiple_points_same_leaf()
    test_choose_leaf_root()
    print("debug_rtree.py: tests parciales pasaron (sin split/query aún)")