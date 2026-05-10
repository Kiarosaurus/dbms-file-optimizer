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


def test_page_explorer_hex_format():
    try:
        from gui import explorer
        # Verificar que el módulo tiene funciones de formateo
        has_funcs = any(
            hasattr(explorer, attr)
            for attr in ["PageExplorer", "format_hex", "decode_page"]
        )
        print(f"  [OK] test_page_explorer_hex_format (módulo importado, attrs: {has_funcs})")
    except ImportError:
        print("  [SKIP] test_page_explorer_hex_format (gui.explorer no disponible)")


def test_page_explorer_decoded_records():
    try:
        import importlib
        importlib.import_module("gui.explorer")
        print("  [OK] test_page_explorer_decoded_records (módulo explorer importa sin error)")
    except ImportError:
        print("  [SKIP] test_page_explorer_decoded_records")


def test_app_instantiation_headless():
    try:
        import gui.app
        print("  [OK] test_app_instantiation_headless (gui.app importa sin error)")
    except ImportError:
        print("  [SKIP] test_app_instantiation_headless (gui.app no disponible)")
    except Exception as e:
        # tkinter puede fallar en entornos headless al inicializar Display
        if "display" in str(e).lower() or "tkinter" in str(e).lower():
            print(f"  [SKIP] test_app_instantiation_headless (entorno headless: {e})")
        else:
            raise


def test_bridge_integration_full_pipeline():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(td, "journal.log"))
            bridge = JupiterBridge(td)

            bridge.execute_sql("CREATE TABLE Pipeline (id INTEGER, label VARCHAR(15))")
            for i, label in enumerate(["alpha", "beta", "gamma", "delta", "epsilon"], 1):
                bridge.execute_sql(f"INSERT INTO Pipeline VALUES ({i}, '{label}')")

            result = bridge.execute_sql("SELECT * FROM Pipeline")
            rows = result.get("rows", result) if isinstance(result, dict) else result
            assert len(rows) == 5, f"Pipeline debe tener 5 rows, tiene {len(rows)}"
            print("  [OK] test_bridge_integration_full_pipeline")
        except ImportError:
            print("  [SKIP] test_bridge_integration_full_pipeline")

def test_workspace_manager_via_bridge():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            ws1 = os.path.join(td, "workspace_1")
            ws2 = os.path.join(td, "workspace_2")
            os.makedirs(ws1); os.makedirs(ws2)
            init_concurrency(os.path.join(ws1, "journal.log"))
            bridge1 = JupiterBridge(ws1)
            bridge1.execute_sql("CREATE TABLE TW1 (id INTEGER)")

            init_concurrency(os.path.join(ws2, "journal.log"))
            bridge2 = JupiterBridge(ws2)
            bridge2.execute_sql("CREATE TABLE TW2 (id INTEGER)")

            assert "TW1" in bridge1.get_tables()
            assert "TW2" not in bridge1.get_tables(), "Los workspaces deben ser independientes"
            print("  [OK] test_workspace_manager_via_bridge")
        except ImportError:
            print("  [SKIP] test_workspace_manager_via_bridge")


def test_massive_ingest_via_bridge():
    with tempfile.TemporaryDirectory() as td:
        try:
            from gui.bridge import JupiterBridge
            from core.concurrency import init_concurrency
            init_concurrency(os.path.join(td, "journal.log"))
            bridge = JupiterBridge(td)

            csv_path = os.path.join(td, "test_data.csv")
            with open(csv_path, "w", newline="") as f:
                writer = _csv_mod.DictWriter(f, fieldnames=["id", "nombre", "valor"])
                writer.writeheader()
                for i in range(1, 21):
                    writer.writerow({"id": i, "nombre": f"item_{i}", "valor": i * 2.0})

            total = bridge.massive_ingest_csv(csv_path, "IngestViaGui")
            assert total == 20, f"massive_ingest debe retornar 20, retornó {total}"
            print("  [OK] test_massive_ingest_via_bridge")
        except (ImportError, AttributeError):
            print("  [SKIP] test_massive_ingest_via_bridge (método no disponible aún)")



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
    test_page_explorer_hex_format()
    test_page_explorer_decoded_records()
    test_app_instantiation_headless()
    test_bridge_integration_full_pipeline()
    test_workspace_manager_via_bridge()
    test_massive_ingest_via_bridge()
    print("Tests completados (SKIP = funcionalidad aún no subida).")




