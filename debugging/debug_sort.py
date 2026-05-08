
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.schema import FieldType, RecordSerializer, SchemaField
from engine.external_sort import ExternalSorter
from engine.operations import merge_join, hash_join, nested_loop_join


def _make_serializer():
    return RecordSerializer([
        SchemaField("id",    FieldType.INTEGER),
        SchemaField("score", FieldType.FLOAT),
    ])


def _make_records(n, reverse=False):
    records = [{"id": i, "score": float(n - i)} for i in range(1, n + 1)]
    if reverse:
        records.sort(key=lambda r: r["id"], reverse=True)
    return records


def test_records_to_flat_file():
    with tempfile.TemporaryDirectory() as td:
        ser = _make_serializer()
        sorter = ExternalSorter(ser, "id", td)
        records = _make_records(30)
        path = os.path.join(td, "flat.bin")
        n = sorter.records_to_flat_file(records, path)
        assert n == 30
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
    print("  [OK] test_records_to_flat_file")


def test_generate_runs():
    with tempfile.TemporaryDirectory() as td:
        ser = _make_serializer()
        sorter = ExternalSorter(ser, "id", td, run_size_pages=1)
        records = _make_records(50, reverse=True)
        flat_path = os.path.join(td, "input.bin")
        sorter.records_to_flat_file(records, flat_path)
        run_info = sorter.generate_runs(flat_path, run_size_pages=1, total_records=50)
        assert len(run_info) > 0, "Debe generar al menos 1 run"
        # verificar que cada run está ordenado
        for rpath, cnt in run_info:
            recs = list(sorter.scan_sorted_file(rpath, cnt))
            ids = [r["id"] for r in recs]
            assert ids == sorted(ids), f"Run {rpath} no está ordenado"
    print("  [OK] test_generate_runs")


def test_multiway_merge():
    with tempfile.TemporaryDirectory() as td:
        ser = _make_serializer()
        sorter = ExternalSorter(ser, "id", td, run_size_pages=1)
        records = _make_records(60, reverse=True)
        flat_in = os.path.join(td, "in.bin")
        flat_out = os.path.join(td, "out.bin")
        sorter.records_to_flat_file(records, flat_in)
        run_info = sorter.generate_runs(flat_in, run_size_pages=1, total_records=60)
        total = sorter.multiway_merge(run_info, flat_out)
        assert total == 60
        result = list(sorter.scan_sorted_file(flat_out, total))
        ids = [r["id"] for r in result]
        assert ids == sorted(ids), "Output del merge no está ordenado"
        assert ids == list(range(1, 61)), "Faltan records en el output"
    print("  [OK] test_multiway_merge")


def test_sort_file_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        ser = _make_serializer()
        sorter = ExternalSorter(ser, "id", td)
        records = _make_records(100, reverse=True)
        flat_in = os.path.join(td, "in.bin")
        flat_out = os.path.join(td, "out.bin")
        sorter.records_to_flat_file(records, flat_in)
        total, n_runs, stats = sorter.sort_file(flat_in, flat_out, total_records=100)
        assert total == 100
        result = list(sorter.scan_sorted_file(flat_out, total))
        ids = [r["id"] for r in result]
        assert ids == list(range(1, 101)), "sort_file no produjo orden correcto"
    print("  [OK] test_sort_file_end_to_end")


def test_merge_join():
    left = [{"T1.id": i, "T1.val": i * 10} for i in range(1, 6)]
    right = [{"T2.id": i, "T2.extra": f"e{i}"} for i in [2, 4, 6]]
    results = list(merge_join(iter(left), iter(right), "T1.id", "T2.id"))
    ids = sorted(r["T1.id"] for r in results)
    assert ids == [2, 4], f"merge_join esperaba [2,4], obtuvo {ids}"
    print("  [OK] test_merge_join")


def test_merge_join_with_duplicates():
    left  = [{"T1.k": 1}, {"T1.k": 1}, {"T1.k": 2}]
    right = [{"T2.k": 1}, {"T2.k": 2}, {"T2.k": 2}]
    results = list(merge_join(iter(left), iter(right), "T1.k", "T2.k"))
    # 1×1 + 1×1 + 1×2 = 4 filas
    assert len(results) == 4, f"merge_join con duplicados: esperaba 4, obtuvo {len(results)}"
    print("  [OK] test_merge_join_with_duplicates")


def test_hash_join():
    left  = [{"A.id": i, "A.v": i}  for i in range(1, 6)]
    right = [{"B.id": i, "B.w": -i} for i in [3, 5, 7]]
    results = list(hash_join(iter(left), iter(right), "A.id", "B.id"))
    ids = sorted(r["A.id"] for r in results)
    assert ids == [3, 5], f"hash_join esperaba [3,5], obtuvo {ids}"
    print("  [OK] test_hash_join")


def test_nested_loop_join():
    left  = [{"X.id": i} for i in range(1, 4)]
    right = [{"Y.id": i} for i in range(2, 5)]
    condition = lambda a, b: a["X.id"] == b["Y.id"]
    results = list(nested_loop_join(iter(left), iter(right), condition))
    ids = sorted(r["X.id"] for r in results)
    assert ids == [2, 3], f"nested_loop_join esperaba [2,3], obtuvo {ids}"
    print("  [OK] test_nested_loop_join")


if __name__ == "__main__":
    print("=== debug_sort.py ===")
    test_records_to_flat_file()
    test_generate_runs()
    test_multiway_merge()
    test_sort_file_end_to_end()
    test_merge_join()
    test_merge_join_with_duplicates()
    test_hash_join()
    test_nested_loop_join()
    print("Todos los tests pasaron.")
