
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.schema import FieldType, RecordSerializer, SchemaField
from indexing.bplus_tree import BPlusTreeIndex, LEAF_HDR_SIZE, NODE_LEAF


def _make_serializer():
    return RecordSerializer([
        SchemaField("id",    FieldType.INTEGER),
        SchemaField("value", FieldType.STRING, str_size=10),
    ])


def _make_tree(tmpdir):
    ser = _make_serializer()
    return BPlusTreeIndex(
        file_path=os.path.join(tmpdir, "btree.bin"),
        serializer=ser,
        key_field="id",
    )



def test_bootstrap_creates_files():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        assert os.path.exists(os.path.join(td, "btree.bin")), "btree.bin no creado"
        root_id, total = tree._read_meta()
        assert root_id == 1, f"root_id esperado 1, obtenido {root_id}"
        assert total == 1, f"total_nodes esperado 1, obtenido {total}"
    print("  [OK] test_bootstrap_creates_files")


def test_read_write_meta():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        tree._write_meta(3, 7)
        root_id, total = tree._read_meta()
        assert root_id == 3
        assert total == 7
    print("  [OK] test_read_write_meta")


def test_root_is_leaf():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        root_id, _ = tree._read_meta()
        page = tree._read_page(root_id)
        assert tree._is_leaf(page), "Root recién creado debe ser leaf"
        n, _ = tree._leaf_header(page)
        assert n == 0, f"Root leaf debe estar vacío, tiene {n} records"
    print("  [OK] test_root_is_leaf")



def test_insert_and_search():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in range(1, 6):
            tree.add({"id": i, "value": f"v{i}"})
        found = tree.search(3)
        assert found is not None
        assert found["id"] == 3
        assert found["value"] == "v3"
    print("  [OK] test_insert_and_search")


def test_search_not_found():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in range(1, 4):
            tree.add({"id": i, "value": f"v{i}"})
        assert tree.search(99) is None
    print("  [OK] test_search_not_found")


def test_insert_order_maintained():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in [5, 3, 1, 4, 2]:
            tree.add({"id": i, "value": f"v{i}"})
        root_id, _ = tree._read_meta()
        current = root_id
        # bajar hasta la hoja más izquierda
        while True:
            page = tree._read_page(current)
            if tree._is_leaf(page):
                break
            current = tree._int_child(page, 0)
        n, _ = tree._leaf_header(page)
        ids = [tree._read_leaf_rec(page, i)["id"] for i in range(n)]
        assert ids == sorted(ids), f"Records en hoja no ordenados: {ids}"
    print("  [OK] test_insert_order_maintained")



def test_split_triggered_on_overflow():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        n = tree.leaf_cap + 1
        for i in range(1, n + 1):
            tree.add({"id": i, "value": f"val{i}"})
        _, total = tree._read_meta()
        assert total > 1, f"Debe haber >1 nodo después del split, hay {total}"
    print("  [OK] test_split_triggered_on_overflow")


def test_range_search_basic():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in range(1, 51):
            tree.add({"id": i, "value": f"v{i}"})
        results = tree.range_search(10, 20)
        ids = sorted(r["id"] for r in results)
        expected = list(range(10, 21))
        assert ids == expected, f"range_search incorrecto: {ids}"
    print("  [OK] test_range_search_basic")


def test_range_search_crosses_leaf_boundary():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in range(1, 201):
            tree.add({"id": i, "value": f"v{i}"})
        results = tree.range_search(90, 110)
        ids = sorted(r["id"] for r in results)
        assert ids == list(range(90, 111)), f"range_search cruce de hoja incorrecto: len={len(ids)}"
    print("  [OK] test_range_search_crosses_leaf_boundary")


def test_remove_existing():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        for i in range(1, 11):
            tree.add({"id": i, "value": f"v{i}"})
        removed = tree.remove(5)
        assert removed >= 1
        assert tree.search(5) is None
    print("  [OK] test_remove_existing")


def test_large_insert_triggers_internal_split():
    with tempfile.TemporaryDirectory() as td:
        tree = _make_tree(td)
        n = tree.leaf_cap * (tree.internal_cap + 2)
        for i in range(1, n + 1):
            tree.add({"id": i, "value": f"v{i}"})
        found = tree.search(n // 2)
        assert found is not None, "search debe encontrar el record del medio tras splits internos"
        assert found["id"] == n // 2
    print("  [OK] test_large_insert_triggers_internal_split")


if __name__ == "__main__":
    print("=== debug_bplus.py ===")
    test_bootstrap_creates_files()
    test_read_write_meta()
    test_root_is_leaf()
    test_insert_and_search()
    test_search_not_found()
    test_insert_order_maintained()
    test_split_triggered_on_overflow()
    test_range_search_basic()
    test_range_search_crosses_leaf_boundary()
    test_remove_existing()
    test_large_insert_triggers_internal_split()
    print("Todos los tests pasaron.")


