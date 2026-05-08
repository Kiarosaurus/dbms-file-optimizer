import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.concurrency import init_concurrency
from engine.core import StorageEngine
from parsing.sql_analyzer import QueryCompiler


def setup_test_db(tmpdir):
    init_concurrency(os.path.join(tmpdir, "journal.log"))
    engine = StorageEngine(tmpdir)
    compiler = QueryCompiler()

    def run(sql):
        for node in compiler.compile(sql):
            engine.execute(node)

    run("CREATE TABLE Estudiantes (id INTEGER, nombre VARCHAR(20), edad INTEGER)")
    run("CREATE TABLE Notas (id INTEGER, id_est INTEGER, nota FLOAT)")

    for i in range(1, 21):
        run(f"INSERT INTO Estudiantes VALUES ({i}, 'Est_{i:02d}', {18 + i % 5})")
    for i in range(1, 21):
        nota = 6.0 + (i % 5)
        run(f"INSERT INTO Notas VALUES ({i}, {i}, {nota})")

    return engine, compiler


def test_end_to_end_select_where():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)
        nodes = comp.compile("SELECT * FROM Estudiantes WHERE id > 15")
        rows = eng.execute(nodes[0])
        assert len(rows) == 5, f"WHERE id>15 debe retornar 5 rows, retornó {len(rows)}"
    print("  [OK] test_end_to_end_select_where")


def test_end_to_end_join():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)
        sql = ("SELECT Estudiantes.nombre, Notas.nota "
               "FROM Estudiantes JOIN Notas ON Estudiantes.id = Notas.id_est")
        rows = eng.execute(comp.compile(sql)[0])
        assert len(rows) == 20, f"JOIN completo debe retornar 20 rows, retornó {len(rows)}"
        # verificar que las columnas esperadas están presentes
        if rows:
            first = rows[0]
            assert any("nombre" in k for k in first.keys()), "Columna nombre no en resultado"
    print("  [OK] test_end_to_end_join")


def test_end_to_end_group_by():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)
        sql = "SELECT id_est, AVG(nota) FROM Notas GROUP BY id_est"
        rows = eng.execute(comp.compile(sql)[0])
        assert len(rows) == 20, f"GROUP BY id_est debe tener 20 grupos (1 por estudiante)"
    print("  [OK] test_end_to_end_group_by")


def test_end_to_end_delete_and_verify():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)

        # eliminar estudiante id=10
        eng.execute(comp.compile("DELETE FROM Estudiantes WHERE id = 10")[0])

        # verificar que no aparece en SELECT
        rows = eng.execute(comp.compile("SELECT * FROM Estudiantes WHERE id = 10")[0])
        assert len(rows) == 0, "Estudiante id=10 debe estar eliminado"

        # verificar que los demás siguen
        all_rows = eng.execute(comp.compile("SELECT * FROM Estudiantes")[0])
        assert len(all_rows) == 19, f"Deben quedar 19 estudiantes, quedan {len(all_rows)}"
    print("  [OK] test_end_to_end_delete_and_verify")


def test_end_to_end_between():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)
        rows = eng.execute(comp.compile("SELECT * FROM Estudiantes WHERE id BETWEEN 5 AND 10")[0])
        assert len(rows) == 6, f"BETWEEN 5 AND 10 debe retornar 6 rows, retornó {len(rows)}"
        ids = sorted(list(r.values())[0] for r in rows)
        assert ids == [5, 6, 7, 8, 9, 10]
    print("  [OK] test_end_to_end_between")


def test_end_to_end_select_columns():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)
        rows = eng.execute(comp.compile("SELECT nombre FROM Estudiantes WHERE id = 1")[0])
        assert len(rows) == 1
        row = rows[0]
        # debe tener solo la columna nombre
        assert len(row) == 1
    print("  [OK] test_end_to_end_select_columns")


def test_parser_to_engine_pipeline():
    with tempfile.TemporaryDirectory() as td:
        init_concurrency(os.path.join(td, "journal.log"))
        eng = StorageEngine(td)
        comp = QueryCompiler()

        sqls = [
            "CREATE TABLE Pipeline (pk INTEGER, label VARCHAR(15))",
            "INSERT INTO Pipeline VALUES (1, 'alpha')",
            "INSERT INTO Pipeline VALUES (2, 'beta')",
            "INSERT INTO Pipeline VALUES (3, 'gamma')",
        ]
        for sql in sqls:
            for node in comp.compile(sql):
                eng.execute(node)

        rows = eng.execute(comp.compile("SELECT * FROM Pipeline")[0])
        assert len(rows) == 3, f"Pipeline test debe tener 3 rows, tiene {len(rows)}"
        labels = [list(r.values())[1] for r in sorted(rows, key=lambda r: list(r.values())[0])]
        assert "alpha" in labels[0]
    print("  [OK] test_parser_to_engine_pipeline")


def test_end_to_end_count_all():
    with tempfile.TemporaryDirectory() as td:
        eng, comp = setup_test_db(td)
        rows = eng.execute(comp.compile("SELECT COUNT(id) FROM Estudiantes")[0])
        assert len(rows) == 1
        count_val = list(rows[0].values())[0]
        assert count_val == 20, f"COUNT debe retornar 20, retornó {count_val}"
    print("  [OK] test_end_to_end_count_all")


if __name__ == "__main__":
    print("=== debug_queries.py ===")
    test_end_to_end_select_where()
    test_end_to_end_join()
    test_end_to_end_group_by()
    test_end_to_end_delete_and_verify()
    test_end_to_end_between()
    test_end_to_end_select_columns()
    test_parser_to_engine_pipeline()
    test_end_to_end_count_all()
    print("Todos los tests de integración pasaron.")
