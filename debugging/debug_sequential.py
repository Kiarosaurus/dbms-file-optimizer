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

def test_remove_existing_key():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for rec in _make_records(10):
            idx.add(rec)
        removed = idx.remove(5)
        assert removed >= 1, f"remove debe retornar al menos 1, retorno {removed}"
        assert idx.search(5) is None, "search(5) debe retornar None post-remove"
    print("  [OK] test_remove_existing_key")


def test_remove_nonexistent_key():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for rec in _make_records(5):
            idx.add(rec)
        removed = idx.remove(999)
        assert removed == 0, f"remove de key inexistente debe retornar 0, retorno {removed}"
    print("  [OK] test_remove_nonexistent_key")


def test_remove_maintains_sort():
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for rec in _make_records(8):
            idx.add(rec)
        idx.reorganize()
        idx.remove(4)
        count = idx._read_count(idx.main_path)
        ids = [idx._read_record(idx.main_path, i)["id"] for i in range(count)]
        assert 4 not in ids, "id=4 debe estar eliminado"
        assert ids == sorted(ids), f"Main debe seguir ordenado: {ids}"
    print("  [OK] test_remove_maintains_sort")

def test_range_search_sorted_main():
    """range_search en main ordenado retorna solo records en [lo, hi]."""
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for rec in _make_records(20):
            idx.add(rec)
        idx.reorganize()
        results = idx.range_search(5, 12)
        ids = [r["id"] for r in results]
        assert ids == list(range(5, 13)), f"Esperado [5..12], got {ids}"
        assert ids == sorted(ids), "Resultado debe estar ordenado"
    print("  [OK] test_range_search_sorted_main")


def test_range_search_includes_overflow():
    """range_search incluye records del overflow que caen en el rango."""
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for i in range(1, 11):
            idx.add({"id": i, "name": f"n_{i:04d}"})
        idx.reorganize()
        # añade al overflow sin disparar reorganize
        idx.add({"id": 25, "name": "n_0025"})
        idx.add({"id": 7,  "name": "n_dup7"})   # duplicado — debe deduplicarse
        results = idx.range_search(5, 28)
        ids = [r["id"] for r in results]
        assert 7  in ids, "id=7 debe estar (main y overflow, deduplicado)"
        assert 25 in ids, "id=25 del overflow debe estar"
        assert 30 not in ids, "id=30 fuera de rango no debe aparecer"
        assert ids == sorted(ids), "Resultado debe estar ordenado"
        assert len(ids) == len(set(ids)), "No debe haber duplicados"
    print("  [OK] test_range_search_includes_overflow")


def test_range_search_empty_result():
    """range_search fuera del rango retorna lista vacía."""
    with tempfile.TemporaryDirectory() as td:
        idx = _make_index(td, k=100)
        for rec in _make_records(5):
            idx.add(rec)
        idx.reorganize()
        assert idx.range_search(100, 200) == [], "Fuera de rango debe retornar []"
    print("  [OK] test_range_search_empty_result")


def test_range_search_engine_pushdown():
    """Engine usa range_search (push-down) para BETWEEN en Sequential y BTree."""
    import tempfile as _tf
    from engine.core import StorageEngine
    from parsing.sql_analyzer import QueryCompiler

    with _tf.TemporaryDirectory() as td:
        eng = StorageEngine(td)
        qc  = QueryCompiler()

        eng.execute(qc.compile("CREATE TABLE S (id INTEGER, value INTEGER)")[0])
        for i in range(1, 21):
            eng.execute(qc.compile(f"INSERT INTO S VALUES ({i}, {i*10})")[0])

        rows = eng.execute(qc.compile("SELECT * FROM S WHERE id BETWEEN 5 AND 10")[0])
        ids  = sorted(r["S.id"] for r in rows)
        assert ids == [5,6,7,8,9,10], f"SEQ push-down falló: {ids}"

        eng.execute(qc.compile("CREATE TABLE B (id INTEGER, value INTEGER) INDEX (BTREE id)")[0])
        for i in range(1, 21):
            eng.execute(qc.compile(f"INSERT INTO B VALUES ({i}, {i*10})")[0])

        rows2 = eng.execute(qc.compile("SELECT * FROM B WHERE id BETWEEN 8 AND 13")[0])
        ids2  = sorted(r["B.id"] for r in rows2)
        assert ids2 == [8,9,10,11,12,13], f"BTREE push-down falló: {ids2}"
    print("  [OK] test_range_search_engine_pushdown")


if __name__ == "__main__":
    print("=== debug_sequential.py ===")
    test_sequential_insert_and_linear_search()
    test_sequential_search_not_found()
    test_sequential_overflow_accumulates()
    test_reorganize_merges_and_sorts()
    test_binary_search_after_reorganize()
    test_auto_reorganize_on_threshold()
    test_remove_existing_key()
    test_remove_nonexistent_key()
    test_remove_maintains_sort()
    test_range_search_sorted_main()
    test_range_search_includes_overflow()
    test_range_search_empty_result()
    test_range_search_engine_pushdown()
    print("Todos los tests pasaron.")
