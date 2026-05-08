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



def test_split_triggered_when_bucket_full():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td, bucket_size=3)
        # insertar más de bucket_cap records
        for i in range(1, 10):
            idx.add({"id": i, "data": f"d{i}"})
        # verificar que todos se encuentran
        for i in range(1, 10):
            assert idx.search(i) is not None, f"id={i} no encontrado tras split"
    print("  [OK] test_split_triggered_when_bucket_full")


def test_directory_doubles():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td, bucket_size=2)
        # forzar múltiples splits
        for i in range(1, 20):
            idx.add({"id": i, "data": f"d{i}"})
        assert idx.global_depth >= 1, "global_depth debe aumentar tras splits"
        assert len(idx.directory) == 2 ** idx.global_depth
    print("  [OK] test_directory_doubles")


def test_large_insert_no_collision():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td, bucket_size=4)
        n = 100
        for i in range(1, n + 1):
            idx.add({"id": i, "data": f"data_{i:03d}"})
        # verificar 20 keys aleatorias
        import random
        rng = random.Random(42)
        sample = rng.sample(range(1, n + 1), 20)
        for k in sample:
            found = idx.search(k)
            assert found is not None, f"id={k} no encontrado en hash grande"
            assert found["id"] == k
    print("  [OK] test_large_insert_no_collision")


def test_directory_state_output():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_hash(td, bucket_size=3)
        for i in range(1, 8):
            idx.add({"id": i, "data": f"d{i}"})
        state = idx.directory_state()
        assert "global_depth" in state
        assert "bucket" in state.lower() or "block" in state.lower()
    print("  [OK] test_directory_state_output")


if __name__ == "__main__":
    print("=== debug_hash.py ===")
    test_bootstrap_single_bucket()
    test_insert_and_search()
    test_search_miss()
    test_remove_record()
    test_hash_consistency()
    test_split_triggered_when_bucket_full()
    test_directory_doubles()
    test_large_insert_no_collision()
    test_directory_state_output()
    print("Todos los tests pasaron.")

