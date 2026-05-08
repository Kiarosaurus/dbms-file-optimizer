
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.schema import FieldType, RecordSerializer, SchemaField
from indexing.extendible_hash import ExtendibleHashIndex


def _make_serializer():
    return RecordSerializer([
        SchemaField("id",   FieldType.INTEGER),
        SchemaField("data", FieldType.STRING, str_size=12),
    ])


def _make_hash(tmpdir, bucket_size=5):
    ser = _make_serializer()
    return ExtendibleHashIndex(
        file_path=os.path.join(tmpdir, "hash.bin"),
        serializer=ser,
        key_field="id",
        bucket_size=bucket_size,
    )


# ─── Commit H+20 ──────────────────────────────────────────────────────────────

def test_bootstrap_single_bucket():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td)
        assert idx.global_depth == 0, f"global_depth inicial debe ser 0, es {idx.global_depth}"
        assert len(idx.directory) == 1, f"directorio debe tener 1 entrada, tiene {len(idx.directory)}"
    print("  [OK] test_bootstrap_single_bucket")


def test_insert_and_search():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td)
        for i in range(1, 4):
            idx.add({"id": i, "data": f"dato_{i}"})
        found = idx.search(2)
        assert found is not None
        assert found["id"] == 2
    print("  [OK] test_insert_and_search")


def test_search_miss():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td)
        for i in range(1, 4):
            idx.add({"id": i, "data": f"d{i}"})
        assert idx.search(999) is None
    print("  [OK] test_search_miss")


def test_remove_record():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td)
        for i in range(1, 4):
            idx.add({"id": i, "data": f"d{i}"})
        removed = idx.remove(2)
        assert removed >= 1
        assert idx.search(2) is None
    print("  [OK] test_remove_record")


def test_hash_consistency():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td)
        h1 = idx.hash_func(42)
        h2 = idx.hash_func(42)
        assert h1 == h2, "hash debe ser determinista"
    print("  [OK] test_hash_consistency")




if __name__ == "__main__":
    test_bootstrap_single_bucket()
    test_insert_and_search()
    test_search_miss()
    test_remove_record()
    test_hash_consistency()
    print("debug_hash.py: tests parciales pasaron (sin split aún)")