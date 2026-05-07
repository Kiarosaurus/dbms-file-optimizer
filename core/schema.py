from __future__ import annotations
import struct
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional

# enum con los tipos soportados en el schema de cada tabla
class FieldType(Enum):
    INTEGER = auto()
    FLOAT   = auto()
    STRING  = auto()
    BIGINT  = auto() #para prevenir overflow en los id largos

@dataclass
class SchemaField:
    name: str
    field_type: FieldType
    str_size: Optional[int] = None   
    # devuelve el format char de struct segun el tipo de campo
    @property
    def struct_char(self) -> str:
        if self.field_type == FieldType.INTEGER:
            return "i"
        if self.field_type == FieldType.FLOAT:
            return "f"
        if self.field_type == FieldType.STRING:
            if not self.str_size or self.str_size < 1:
                raise ValueError(f"SchemaField '{self.name}': str_size must be >= 1")
            return f"{self.str_size}s"
        if self.field_type == FieldType.BIGINT:
            return "q"   
        raise ValueError(f"Unknown FieldType: {self.field_type}")
# serializa y deserializa records a binario de ancho fijo usando struct con network byte order
class RecordSerializer:
    _BYTE_ORDER = "!"
    # precalcula el format string y el record_size al construir
    def __init__(self, fields: List[SchemaField]):
        self.fields     = fields
        self._fmt       = self._BYTE_ORDER + "".join(f.struct_char for f in fields)
        self.record_size = struct.calcsize(self._fmt)
    # convierte un dict a bytes empaquetados y normaliza strings a bytes con padding null
    def serialize(self, values_dict: Dict[str, Any]) -> bytes:
        packed_values = []
        for field in self.fields:
            value = values_dict[field.name]
            if field.field_type == FieldType.STRING:
                # acepta str, bytes, bytearray o cualquier otro
                if isinstance(value, str):
                    value = value.encode("utf-8")
                elif not isinstance(value, (bytes, bytearray)):
                    value = str(value).encode("utf-8")
                # trunca al ancho declarado sin cortar a mitad de secuencia 
                if len(value) > field.str_size:
                    trunc = bytearray(value[: field.str_size])
                    while trunc and (trunc[-1] & 0xC0) == 0x80:   
                        trunc.pop()
                    if trunc and trunc[-1] >= 0xC0:              
                        trunc.pop()
                    value = bytes(trunc)
                value = value.ljust(field.str_size, b"\x00")
            elif field.field_type in (FieldType.INTEGER, FieldType.BIGINT):
                value = int(value)
            elif field.field_type == FieldType.FLOAT:
                value = float(value)
            packed_values.append(value)
        return struct.pack(self._fmt, *packed_values)
    # convierte bytes a dict. Dando lugar a strips null padding en strings
    def deserialize(self, byte_data: bytes) -> Dict[str, Any]:
        unpacked = struct.unpack(self._fmt, byte_data[: self.record_size])
        result: Dict[str, Any] = {}
        for i, field in enumerate(self.fields):
            value = unpacked[i]
            if field.field_type == FieldType.STRING:
                value = value.rstrip(b"\x00").decode("utf-8", errors="replace")
            result[field.name] = value
        return result

