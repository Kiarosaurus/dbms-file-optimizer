import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.schema import FieldType, RecordSerializer, SchemaField
from indexing.sequential_file import SequentialIndex

def _make_serializer():
    return RecordSerializer([
        SchemaField("id",   FieldType.INTEGER),
        SchemaField("name", FieldType.STRING, str_size=15),
    ])

def _make_index(tmpdir, k=10):
    ser = _make_serializer()
    return SequentialIndex(
        main_path=os.path.join(tmpdir, "main.bin"),
        overflow_path=os.path.join(tmpdir, "ovfl.bin"),
        serializer=ser,
        K_threshold=k,
    )

def _make_records(n):
    return [{"id": i, "name": f"nombre_{i:04d}"} for i in range(1, n + 1)]

def test_sequential_insert_and_linear_search():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td)
        for rec in _make_records(5):
            idx.add(rec)
        found = idx.search(3)
        assert found is not None, "search(3) no deberia retornar None"
        assert found["id"] == 3
    print("  [OK] test_sequential_insert_and_linear_search")


def test_sequential_search_not_found():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td)
        for rec in _make_records(5):
            idx.add(rec)
        assert idx.search(99) is None, "search de key inexistente debe retornar None"
    print("  [OK] test_sequential_search_not_found")


def test_sequential_overflow_accumulates():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)  # threshold alto para forzar overflow manual
        for rec in _make_records(8):
            idx.add(rec)
        count = idx._read_count(idx.overflow_path)
        assert count == 8, f"Overflow debe tener 8 records, tiene {count}"
    print("  [OK] test_sequential_overflow_accumulates")

if __name__ == "__main__":
    test_sequential_insert_and_linear_search()
    test_sequential_search_not_found()
    test_sequential_overflow_accumulates()
    print("debug_sequential.py: tests parciales pasaron")