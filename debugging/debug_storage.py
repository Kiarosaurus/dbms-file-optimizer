import os
import sys
import tempfile
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from core.storage import DiskController, PAGE_SIZE
from core.schema import FieldType, RecordSerializer, SchemaField
from core.metrics import Telemetry
from core.workspace_manager import WorkspaceManager

def test_write_read_block():
    with tempfile.TemporaryDirectory() as td:
        disk = DiskController()
        path = os.path.join(td, "test.bin")
        data = b"JupiterDB" + b"\x00" * (PAGE_SIZE - 9)
        disk.write_block(path, 0, data)
        read_back = disk.read_block(path, 0)
        assert read_back[:9] == b"JupiterDB", "Datos leidos no coinciden"
        assert len(read_back) == PAGE_SIZE, f"Page size incorrecto: {len(read_back)}"
    print("  [OK] test_write_read_block")

def test_page_size_constraint():
    with tempfile.TemporaryDirectory() as td:
        disk = DiskController()
        path = os.path.join(td, "test.bin")
        oversized = b"X" * (PAGE_SIZE + 1)
        try:
            disk.write_block(path, 0, oversized)
            assert False, "Debia lanzar ValueError"
        except ValueError:
            pass
    print("  [OK] test_page_size_constraint")

def test_telemetry_counting():
    tel = Telemetry()
    tel.reset()
    with tempfile.TemporaryDirectory() as td:
        disk = DiskController()
        path = os.path.join(td, "tel.bin")
        disk.write_block(path, 0, b"a")
        disk.write_block(path, 1, b"b")
        disk.read_block(path, 0)
        assert tel.pages_written >= 2, f"Escrituras esperadas >= 2, obtenidas: {tel.pages_written}"
        assert tel.pages_read >= 1, f"Lecturas esperadas >= 1, obtenidas: {tel.pages_read}"
    print("  [OK] test_telemetry_counting")

def test_record_serializer_roundtrip():
    fields = [
        SchemaField("id",    FieldType.INTEGER),
        SchemaField("name",  FieldType.STRING,  str_size=20),
        SchemaField("score", FieldType.FLOAT),
    ]
    ser = RecordSerializer(fields)
    original = {"id": 42, "name": "Ana", "score": 9.75}
    raw = ser.serialize(original)
    recovered = ser.deserialize(raw)
    assert recovered["id"] == 42
    assert recovered["name"] == "Ana"
    assert abs(recovered["score"] - 9.75) < 0.01
    print("  [OK] test_record_serializer_roundtrip")

def test_workspace_manager_create_project():
    with tempfile.TemporaryDirectory() as td:
        wm = WorkspaceManager(td)
        path = wm.ensure_project("mi_proyecto")
        assert os.path.isdir(path), "Directorio del proyecto no creado"
        catalog = os.path.join(path, "catalog.json")
        assert os.path.exists(catalog), "catalog.json no creado"
    print("  [OK] test_workspace_manager_create_project")


def test_workspace_manager_list_projects():
    with tempfile.TemporaryDirectory() as td:
        wm = WorkspaceManager(td)
        wm.ensure_project("proyecto_a")
        wm.ensure_project("proyecto_b")
        projects = wm.get_projects()
        assert "proyecto_a" in projects
        assert "proyecto_b" in projects
    print("  [OK] test_workspace_manager_list_projects")


def test_workspace_manager_default():
    with tempfile.TemporaryDirectory() as td:
        wm = WorkspaceManager(td)
        path = wm.ensure_default()
        assert "default_testing" in path
        assert os.path.isdir(path)
    print("  [OK] test_workspace_manager_default")


if __name__ == "__main__":
    print("=== debug_storage.py ===")
    test_write_read_block()
    test_page_size_constraint()
    test_telemetry_counting()
    test_record_serializer_roundtrip()
    test_workspace_manager_create_project()
    test_workspace_manager_list_projects()
    test_workspace_manager_default()
    print("Todos los tests pasaron.")
