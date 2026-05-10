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
        if count >= self.K_threshold:
            self.reorganize()
    
    # binary search en main, luego linear scan en overflow
    def search(self, key: Any) -> Optional[Dict[str, Any]]:
        result = self._binary_search_main(key)
        if result is not None:
            return result
        return self._linear_scan_overflow(key)
    

    # elimina por key de ambos archivos — una página leida una sola vez
    def remove(self, key: Any) -> int:
        removed = 0

        main_count = self._read_count(self.main_path)
        tmp_main   = self.main_path + ".tmp"

        def _filter_main():
            nonlocal removed
            for rec in self._scan_records(self.main_path, main_count):
                if rec[self.key_field] == key:
                    removed += 1
                else:
                    yield rec

        self._write_records_sequential(tmp_main, _filter_main())
        os.replace(tmp_main, self.main_path)

        ovfl_count = self._read_count(self.overflow_path)
        tmp_ovfl   = self.overflow_path + ".tmp"

        def _filter_ovfl():
            nonlocal removed
            for rec in self._scan_records(self.overflow_path, ovfl_count):
                if rec[self.key_field] == key:
                    removed += 1
                else:
                    yield rec

        self._write_records_sequential(tmp_ovfl, _filter_ovfl())
        os.replace(tmp_ovfl, self.overflow_path)

        return removed
    

     # merge de main (sorted) + overflow (≤K records) — cada pagina leida una sola vez
    def reorganize(self) -> None:
        overflow_count = self._read_count(self.overflow_path)

        # Overflow acotado por K_threshold — O(K) en RAM, no O(N)
        ovfl = list(self._scan_records(self.overflow_path, overflow_count))
        ovfl.sort(key=lambda r: r[self.key_field])

        main_count = self._read_count(self.main_path)
        tmp_path   = self.main_path + ".tmp"

        def _merged():
            main_iter = self._scan_records(self.main_path, main_count)
            ovfl_iter = iter(ovfl)
            cur_m = next(main_iter, None)
            cur_o = next(ovfl_iter, None)
            while cur_m is not None or cur_o is not None:
                if cur_m is not None and (
                    cur_o is None or cur_m[self.key_field] <= cur_o[self.key_field]
                ):
                    yield cur_m
                    cur_m = next(main_iter, None)
                else:
                    yield cur_o
                    cur_o = next(ovfl_iter, None)

        self._write_records_sequential(tmp_path, _merged())
        os.replace(tmp_path, self.main_path)
        self._write_count(self.overflow_path, 0)
        self.reorganize_count += 1
    

    # ─── Page-batched I/O helpers ──────────────────────────────────────────────

    # genera records leyendo ceil(count/RPP) páginas — cada página leída UNA vez
    def _scan_records(self, path: str, count: int):
        if count == 0:
           return
        n_pages   = (count + self.records_per_page - 1) // self.records_per_page
        remaining = count
        for page_num in range(n_pages):
            block_id = page_num + 1          # block 0 es el header
            page     = self._load_page(path, block_id)
            recs_here = min(self.records_per_page, remaining)
            for slot in range(recs_here):
                off = slot * self.record_size
                yield self.serializer.deserialize(bytes(page[off: off + self.record_size]))
            remaining -= recs_here

    # escribe records page-by-page desde un iterable; retorna total escrito
    def _write_records_sequential(self, path: str, records_iter) -> int:
        if not os.path.exists(path):
            self._write_count(path, 0)       # crea el archivo con header vacío
        page_buf = bytearray(PAGE_SIZE)
        slot = 0; block_id = 1; count = 0
        for rec in records_iter:
            off = slot * self.record_size
            page_buf[off: off + self.record_size] = self.serializer.serialize(rec)
            slot  += 1
            count += 1
            if slot == self.records_per_page:
                self.disk.write_block(path, block_id, bytes(page_buf))
                page_buf = bytearray(PAGE_SIZE)
                slot = 0; block_id += 1
        if slot > 0:
            self.disk.write_block(path, block_id, bytes(page_buf))
        self._write_count(path, count)
        return count
    
     # busca la key en main con binary search
    def _binary_search_main(self, key: Any) -> Optional[Dict[str, Any]]:
        count = self._read_count(self.main_path)
        lo, hi = 0, count - 1
        while lo <= hi:
            mid     = (lo + hi) // 2
            record  = self._read_record(self.main_path, mid)
            rec_key = record[self.key_field]
            if rec_key == key:
                return record
            if rec_key < key:
                lo = mid + 1
            else:
                hi = mid - 1
        return None
    
    # binary search en main para primer idx con key >= start_key; scan overflow lineal
    def range_search(self, start_key: Any, end_key: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        # ── Main (sorted): binary search al primer record >= start_key ──
        count = self._read_count(self.main_path)
        if count > 0:
            lo, hi, first = 0, count - 1, count
            while lo <= hi:
                mid = (lo + hi) // 2
                if self._read_record(self.main_path, mid)[self.key_field] < start_key:
                    lo = mid + 1
                else:
                    first = mid
                    hi = mid - 1
            for i in range(first, count):
                rec = self._read_record(self.main_path, i)
                if rec[self.key_field] > end_key:
                    break
                results.append(rec)

        # ── linear scan ──
        for i in range(self._read_count(self.overflow_path)):
            rec = self._read_record(self.overflow_path, i)
            k   = rec[self.key_field]
            if start_key <= k <= end_key:
                results.append(rec)

        # overflow puede duplicar main antes del próximo reorganize
        seen: set = set()
        unique: List[Dict[str, Any]] = []
        for rec in sorted(results, key=lambda r: r[self.key_field]):
            k = rec[self.key_field]
            if k not in seen:
                seen.add(k)
                unique.append(rec)
        return unique

    
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

