import os
import sys
import struct

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.schema import FieldType, RecordSerializer, SchemaField

def test_string_truncation():
    fields = [SchemaField("tag", FieldType.STRING, str_size=5)]
    ser = RecordSerializer(fields)
    raw = ser.serialize({"tag": "hola_mundo_largo"})
    recovered = ser.deserialize(raw)
    assert len(recovered["tag"]) <= 5, f"String no truncado: '{recovered['tag']}'"
    print("  [OK] test_string_truncation")

def test_bigint_range():
    fields = [SchemaField("bigval", FieldType.BIGINT)]
    ser = RecordSerializer(fields)
    large = 9_999_999_999
    raw = ser.serialize({"bigval": large})
    recovered = ser.deserialize(raw)
    assert recovered["bigval"] == large, f"BIGINT no conservado: {recovered['bigval']}"
    print("  [OK] test_bigint_range")

def test_mixed_schema_roundtrip():
    fields = [
        SchemaField("pk",   FieldType.INTEGER),
        SchemaField("uid",  FieldType.BIGINT),
        SchemaField("val",  FieldType.FLOAT),
        SchemaField("name", FieldType.STRING, str_size=20),
    ]
    ser = RecordSerializer(fields)
    rec = {"pk": 1, "uid": 123456789012, "val": 3.14, "name": "test"}
    raw = ser.serialize(rec)
    got = ser.deserialize(raw)
    assert got["pk"] == 1
    assert got["uid"] == 123456789012
    assert abs(got["val"] - 3.14) < 0.001
    assert got["name"] == "test"
    print("  [OK] test_mixed_schema_roundtrip")

def test_null_padding_stripped():
    fields = [SchemaField("s", FieldType.STRING, str_size=10)]
    ser = RecordSerializer(fields)
    raw = ser.serialize({"s": "hi"})
    got = ser.deserialize(raw)
    assert "\x00" not in got["s"], "Null bytes no eliminados"
    assert got["s"] == "hi"
    print("  [OK] test_null_padding_stripped")


def test_invalid_str_size_raises():
    field = SchemaField("x", FieldType.STRING, str_size=0)
    try:
        _ = field.struct_char
        assert False, "Debia lanzar ValueError"
    except ValueError:
        pass
    print("  [OK] test_invalid_str_size_raises")

def test_record_size_consistent():
    fields = [
        SchemaField("a", FieldType.INTEGER),
        SchemaField("b", FieldType.STRING, str_size=10),
    ]
    ser = RecordSerializer(fields)
    # INTEGER = 4 bytes, STRING(10) = 10 bytes → total = 14
    assert ser.record_size == 14, f"record_size incorrecto: {ser.record_size}"
    print("  [OK] test_record_size_consistent")


def test_negative_integer():
    fields = [SchemaField("n", FieldType.INTEGER)]
    ser = RecordSerializer(fields)
    raw = ser.serialize({"n": -999})
    got = ser.deserialize(raw)
    assert got["n"] == -999
    print("  [OK] test_negative_integer")

if __name__ == "__main__":
    print("=== debug_schema.py ===")
    test_string_truncation()
    test_bigint_range()
    test_mixed_schema_roundtrip()
    test_null_padding_stripped()
    test_invalid_str_size_raises()
    test_record_size_consistent()    
    test_negative_integer()
    print("Todos los tests pasaron.")