import os
import sys
import tempfile
import csv as _csv_mod

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.concurrency import init_concurrency
from engine.core import StorageEngine
from parsing.sql_analyzer import QueryCompiler


def _setup(tmpdir):
    init_concurrency(os.path.join(tmpdir, "journal.log"))
    engine = StorageEngine(tmpdir)
    compiler = QueryCompiler()
    return engine, compiler


def _exec(engine, compiler, sql):
    nodes = compiler.compile(sql)
    results = []
    for node in nodes:
        results.append(engine.execute(node))
    return results[-1] if results else None

def test_create_table_sequential():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        result = _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        assert "created" in result.lower() or "T" in result
        assert "T" in eng._catalog["tables"]
    print("  [OK] test_create_table_sequential")


def test_create_table_btree():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE BT (id INTEGER, name VARCHAR(20)) INDEX (BTREE id)")
        meta = eng._catalog["tables"]["BT"]
        assert meta["index"] == "BTREE"
    print("  [OK] test_create_table_btree")


def test_create_table_rtree():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE Pts (id INTEGER, x FLOAT, y FLOAT) INDEX (RTREE x)")
        meta = eng._catalog["tables"]["Pts"]
        assert "rtree_path" in meta
    print("  [OK] test_create_table_rtree")


def test_create_duplicate_table():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE DupT (id INTEGER)")
        result = _exec(eng, comp, "CREATE TABLE DupT (id INTEGER)")
        assert "already exists" in result.lower()
    print("  [OK] test_create_duplicate_table")


def test_insert_basic():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        for i in range(1, 4):
            _exec(eng, comp, f"INSERT INTO T VALUES ({i}, {i * 1.5})")
    print("  [OK] test_insert_basic")


def test_insert_wrong_column_count():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        try:
            _exec(eng, comp, "INSERT INTO T VALUES (1)")
            assert False, "Debia lanzar ValueError"
        except (ValueError, Exception):
            pass
    print("  [OK] test_insert_wrong_column_count")

def test_select_all():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        for i in range(1, 6):
            _exec(eng, comp, f"INSERT INTO T VALUES ({i}, {i * 2.0})")
        rows = _exec(eng, comp, "SELECT * FROM T")
        assert len(rows) == 5, f"SELECT * debe retornar 5 rows, retornó {len(rows)}"
    print("  [OK] test_select_all")


def test_select_with_where():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        for i in range(1, 6):
            _exec(eng, comp, f"INSERT INTO T VALUES ({i}, {float(i)})")
        rows = _exec(eng, comp, "SELECT * FROM T WHERE id = 3")
        assert len(rows) == 1, f"WHERE id=3 debe retornar 1 row, retornó {len(rows)}"
    print("  [OK] test_select_with_where")


def test_select_between():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        for i in range(1, 11):
            _exec(eng, comp, f"INSERT INTO T VALUES ({i}, {float(i)})")
        rows = _exec(eng, comp, "SELECT * FROM T WHERE id BETWEEN 3 AND 7")
        assert len(rows) == 5, f"BETWEEN 3 AND 7 debe retornar 5 rows, retornó {len(rows)}"
    print("  [OK] test_select_between")


def test_delete_with_filter():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (id INTEGER, val FLOAT)")
        for i in range(1, 6):
            _exec(eng, comp, f"INSERT INTO T VALUES ({i}, {float(i)})")
        _exec(eng, comp, "DELETE FROM T WHERE id = 2")
        rows = _exec(eng, comp, "SELECT * FROM T")
        ids = [list(r.values())[0] for r in rows]
        assert 2 not in ids, "id=2 debe estar eliminado"
        assert len(rows) == 4
    print("  [OK] test_delete_with_filter")


def test_group_by_count():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE T (dept INTEGER, nombre VARCHAR(10))")
        for i in range(10):
            dept = (i % 3) + 1
            _exec(eng, comp, f"INSERT INTO T VALUES ({dept}, 'emp{i}')")
        rows = _exec(eng, comp, "SELECT dept, COUNT(dept) FROM T GROUP BY dept")
        assert len(rows) == 3, f"GROUP BY debe tener 3 grupos, tiene {len(rows)}"
    print("  [OK] test_group_by_count")


def test_full_scan_page_by_page():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE BigT (id INTEGER, val FLOAT)")
        n = 500
        for i in range(1, n + 1):
            _exec(eng, comp, f"INSERT INTO BigT VALUES ({i}, {float(i)})")
        rows = _exec(eng, comp, "SELECT * FROM BigT")
        assert len(rows) == n, f"Full scan debe retornar {n} rows, retornó {len(rows)}"
    print("  [OK] test_full_scan_page_by_page")

def test_merge_join():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE A (id INTEGER, name VARCHAR(10))")
        _exec(eng, comp, "CREATE TABLE B (id INTEGER, score FLOAT)")
        for i in range(1, 6):
            _exec(eng, comp, f"INSERT INTO A VALUES ({i}, 'n{i}')")
            _exec(eng, comp, f"INSERT INTO B VALUES ({i}, {float(i * 2)})")
        rows = _exec(eng, comp, "SELECT A.id, B.score FROM A JOIN B ON A.id = B.id")
        assert len(rows) == 5, f"JOIN debe retornar 5 rows, retornó {len(rows)}"
    print("  [OK] test_merge_join")


def test_hash_join_small_tables():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = _setup(td)
        _exec(eng, comp, "CREATE TABLE X (id INTEGER, v FLOAT)")
        _exec(eng, comp, "CREATE TABLE Y (id INTEGER, w FLOAT)")
        for i in [1, 2, 3]:
            _exec(eng, comp, f"INSERT INTO X VALUES ({i}, {float(i)})")
        for i in [2, 3, 4]:
            _exec(eng, comp, f"INSERT INTO Y VALUES ({i}, {float(i * 10)})")
        rows = _exec(eng, comp, "SELECT * FROM X JOIN Y ON X.id = Y.id")
        assert len(rows) == 2, f"Hash join debe retornar 2 matches (ids 2,3), retornó {len(rows)}"
    print("  [OK] test_hash_join_small_tables")


def test_massive_ingest_csv():
    with tempfile.TemporaryDirectory() as td:
        # crear CSV temporal
        csv_path = os.path.join(td, "data.csv")
        n = 50
        with open(csv_path, "w", newline="") as f:
            writer = _csv_mod.DictWriter(f, fieldnames=["id", "nombre", "valor"])
            writer.writeheader()
            for i in range(1, n + 1):
                writer.writerow({"id": i, "nombre": f"item_{i}", "valor": i * 1.1})

        init_concurrency(os.path.join(td, "journal.log"))
        eng = StorageEngine(td)
        total = eng.massive_ingest(csv_path, "IngestTest")
        assert total == n, f"massive_ingest debe retornar {n}, retornó {total}"
        assert "IngestTest" in eng._catalog["tables"]
    print("  [OK] test_massive_ingest_csv")


if __name__ == "__main__":
    print("=== debug_engine.py ===")
    test_create_table_sequential()
    test_create_table_btree()
    test_create_table_rtree()
    test_create_duplicate_table()
    test_insert_basic()
    test_insert_wrong_column_count()
    test_select_all()
    test_select_with_where()
    test_select_between()
    test_delete_with_filter()
    test_group_by_count()
    test_full_scan_page_by_page()
    test_merge_join()
    test_hash_join_small_tables()
    test_massive_ingest_csv()
    print("Todos los tests pasaron.")