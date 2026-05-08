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

def test_reorganize_merges_and_sorts():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        # insertar en orden invertido
        for i in range(10, 0, -1):
            idx.add({"id": i, "name": f"n_{i}"})
        idx.reorganize()
        # overflow debe quedar vacío
        assert idx._read_count(idx.overflow_path) == 0, "Overflow debe estar vacio post-reorganize"
        # main debe estar ordenado: leer todos y verificar orden ascendente
        count = idx._read_count(idx.main_path)
        ids = [idx._read_record(idx.main_path, i)["id"] for i in range(count)]
        assert ids == sorted(ids), f"Main no ordenado: {ids}"
    print("  [OK] test_reorganize_merges_and_sorts")


def test_binary_search_after_reorganize():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for rec in _make_records(20):
            idx.add(rec)
        idx.reorganize()
        found = idx._binary_search_main(10)
        assert found is not None, "Binary search debe encontrar id=10"
        assert found["id"] == 10
    print("  [OK] test_binary_search_after_reorganize")


def test_auto_reorganize_on_threshold():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=5)
        # insertar exactamente K records — el último debe disparar reorganize
        for rec in _make_records(5):
            idx.add(rec)
        # reorganize se dispara automáticamente cuando count >= K
        ovfl = idx._read_count(idx.overflow_path)
        assert ovfl == 0, f"Overflow debe estar vacio tras auto-reorganize, tiene {ovfl}"
    print("  [OK] test_auto_reorganize_on_threshold")