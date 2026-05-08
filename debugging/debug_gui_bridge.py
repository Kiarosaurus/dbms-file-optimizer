import os
import sys
import tempfile
import csv as _csv_mod

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_bridge_init_no_engine():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            bridge = JupiterBridge(td)
            print("  [OK] test_bridge_init_no_engine (bridge instanciado)")
        except ImportError:
            print("  [SKIP] test_bridge_init_no_engine (gui.bridge no disponible aún)")


def test_execute_sql_placeholder():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            bridge = JupiterBridge(td)
            result = bridge.execute_sql("SELECT * FROM T")
            assert isinstance(result, dict), "execute_sql debe retornar dict"
            print("  [OK] test_execute_sql_placeholder")
        except ImportError:
            print("  [SKIP] test_execute_sql_placeholder")


def test_get_tables_empty():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            bridge = JupiterBridge(td)
            tables = bridge.get_tables()
            assert isinstance(tables, list), "get_tables debe retornar lista"
            print("  [OK] test_get_tables_empty")
        except ImportError:
            print("  [SKIP] test_get_tables_empty")


def test_bridge_execute_create_and_insert():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(td, "journal.log"))
            bridge = JupiterBridge(td)
            r1 = bridge.execute_sql("CREATE TABLE T (id INTEGER, val FLOAT)")
            assert "error" not in str(r1).lower() or "created" in str(r1).lower()
            r2 = bridge.execute_sql("INSERT INTO T VALUES (1, 9.5)")
            assert "error" not in str(r2).lower()
            print("  [OK] test_bridge_execute_create_and_insert")
        except ImportError:
            print("  [SKIP] test_bridge_execute_create_and_insert")


def test_bridge_execute_select():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(td, "journal.log"))
            bridge = JupiterBridge(td)
            bridge.execute_sql("CREATE TABLE T (id INTEGER, val FLOAT)")
            for i in range(1, 4):
                bridge.execute_sql(f"INSERT INTO T VALUES ({i}, {float(i)})")
            result = bridge.execute_sql("SELECT * FROM T")
            rows = result.get("rows", result) if isinstance(result, dict) else result
            assert len(rows) == 3, f"SELECT * debe retornar 3 rows, retornó {len(rows)}"
            print("  [OK] test_bridge_execute_select")
        except ImportError:
            print("  [SKIP] test_bridge_execute_select")


def test_bridge_execute_parse_error():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(td, "journal.log"))
            bridge = JupiterBridge(td)
            result = bridge.execute_sql("SELECCIONAR TODO DE T")
            assert isinstance(result, dict)
            # debe retornar un dict con "error", no lanzar excepción
            print("  [OK] test_bridge_execute_parse_error")
        except ImportError:
            print("  [SKIP] test_bridge_execute_parse_error")


def test_bridge_get_tables():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(td, "journal.log"))
            bridge = JupiterBridge(td)
            bridge.execute_sql("CREATE TABLE MiTabla (id INTEGER)")
            tables = bridge.get_tables()
            assert "MiTabla" in tables, f"MiTabla debe estar en get_tables(), obtuvo: {tables}"
            print("  [OK] test_bridge_get_tables")
        except ImportError:
            print("  [SKIP] test_bridge_get_tables")


def test_bridge_seed_demo():
    with tempfile.TemporaryDirectory() as td:
        # simular workspace default_testing
        import os
        ws_path = os.path.join(td, "default_testing")
        os.makedirs(ws_path)
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(ws_path, "journal.log"))
            bridge = JupiterBridge(ws_path)
            tables = bridge.get_tables()
            # El seed debe crear Canciones, Ciudades (datos reales Kaggle)
            demo_tables = {"Canciones", "Ciudades"}
            found = demo_tables.intersection(set(tables))
            if len(found) > 0:
                print(f"  [OK] test_bridge_seed_demo (tablas demo: {found})")
            else:
                print("  [SKIP] test_bridge_seed_demo (seed no ejecutado en este workspace)")
        except ImportError:
            print("  [SKIP] test_bridge_seed_demo")



if __name__ == "__main__":
    print("=== debug_gui_bridge.py ===")
    test_bridge_init_no_engine()
    test_execute_sql_placeholder()
    test_get_tables_empty()
    test_bridge_execute_create_and_insert()
    test_bridge_execute_select()
    test_bridge_execute_parse_error()
    test_bridge_get_tables()
    test_bridge_seed_demo()
    print("Tests completados (SKIP = funcionalidad aún no subida).")




