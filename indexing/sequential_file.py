from __future__ import annotations
import os
import struct
from typing import Any, Dict, List, Optional

from core.storage import DiskController, PAGE_SIZE
from core.schema import RecordSerializer
from indexing.base_index import BaseIndex


# block 0 reservado para header de 4 bytes con el record count
_HDR_FMT  = "!I"                        
_HDR_SIZE = struct.calcsize(_HDR_FMT)   

# sequential file index con overflow area, main siempre sorted
class SequentialIndex(BaseIndex):

    # inicializa paths, serializer y crea archivos si no existen
    def __init__(
        self,
        main_path: str,
        overflow_path: str,
        serializer: RecordSerializer,
        K_threshold: int = 5,
    ):
        self.main_path      = main_path
        self.overflow_path  = overflow_path
        self.serializer     = serializer
        self.record_size    = serializer.record_size
        self.K_threshold    = K_threshold
        self.disk           = DiskController()
        self.key_field      = serializer.fields[0].name
        self.records_per_page = PAGE_SIZE // self.record_size

        self.reorganize_count = 0    
        self._ensure_file(self.main_path)
        self._ensure_file(self.overflow_path)

    # append al overflow y dispara reorganize si se alcanza k_threshold
    def add(self, data_dict: Dict[str, Any]) -> None:
        count = self._read_count(self.overflow_path)
        raw   = self.serializer.serialize(data_dict)
        self._write_record(self.overflow_path, count, raw)
        count += 1
        self._write_count(self.overflow_path, count)
        # Falta disparar reorganize() cuando count >= threshold
    
    def search(self, key):
    # por ahora solo scan lineal sobre overflow
    # TODO: agregar binary search sobre main 
        return self._linear_scan_overflow(key)
    
    # scan lineal sobre overflow buscando la key
    def _linear_scan_overflow(self, key: Any) -> Optional[Dict[str, Any]]:
        count = self._read_count(self.overflow_path)
        for i in range(count):
            record = self._read_record(self.overflow_path, i)
            if record[self.key_field] == key:
                return record
        return None
    
     # mapea logical index a (block_id, byte_offset) dentro del page
    def _record_location(self, logical_index: int):
        page_slot = logical_index // self.records_per_page
        offset    = (logical_index % self.records_per_page) * self.record_size
        return page_slot + 1, offset   # +1 skips the header block

    # lee un record por indice logico desde el path dado
    def _read_record(self, path: str, logical_index: int) -> Dict[str, Any]:
        block_id, offset = self._record_location(logical_index)
        page = self._load_page(path, block_id)
        raw  = bytes(page[offset : offset + self.record_size])
        return self.serializer.deserialize(raw)

    # escribe raw bytes de un record en su slot de page
    def _write_record(self, path: str, logical_index: int, raw: bytes) -> None:
        block_id, offset = self._record_location(logical_index)
        page = self._load_page(path, block_id)
        page[offset : offset + self.record_size] = raw
        self.disk.write_block(path, block_id, bytes(page))

    def _read_count(self, path: str) -> int:
        page = self._load_page(path, 0)
        return struct.unpack(_HDR_FMT, page[:_HDR_SIZE])[0]

    # serializa el count en los primeros 4 bytes del header block
    def _write_count(self, path: str, count: int) -> None:
        page = self._load_page(path, 0)
        page[:_HDR_SIZE] = struct.pack(_HDR_FMT, count)
        self.disk.write_block(path, 0, bytes(page))

    # lee un page de 4096 bytes, retorna bytearray zeroed si no existe
    def _load_page(self, path: str, block_id: int) -> bytearray:
        if not os.path.exists(path):
            return bytearray(PAGE_SIZE)
        try:
            return bytearray(self.disk.read_block(path, block_id))
        except Exception:
            return bytearray(PAGE_SIZE)

    # crea el archivo con header count=0 si es nuevo
    def _ensure_file(self, path: str) -> None:
        if not os.path.exists(path):
            self._write_count(path, 0)

