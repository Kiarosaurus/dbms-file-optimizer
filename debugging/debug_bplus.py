
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.schema import FieldType, RecordSerializer, SchemaField
from indexing.bplus_tree import BPlusTreeIndex, LEAF_HDR_SIZE, NODE_LEAF



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
