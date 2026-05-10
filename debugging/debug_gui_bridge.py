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



if __name__ == "__main__":
    print("=== debug_gui_bridge.py ===")
    test_bridge_init_no_engine()
    test_execute_sql_placeholder()
    test_get_tables_empty()
    print("Tests completados (SKIP = funcionalidad aún no subida).")




