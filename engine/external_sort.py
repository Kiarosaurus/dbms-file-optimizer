from __future__ import annotations

import heapq
import os
from typing import Any, Dict, Iterator, List, Tuple

from core.schema import RecordSerializer
from core.storage import DiskController, PAGE_SIZE


# reader bufferizado para un run file binario plano, fetches una page a la vez via DiskController
class _RunReader:

    # inicializa punteros y precalcula records per page; total_records evita contar slots de padding
    def __init__(
        self,
        path: str,
        serializer: RecordSerializer,
        disk: DiskController,
        total_records: int = None,
    ):
        self.path       = path
        self.serializer = serializer
        self.disk       = disk
        self.rsize      = serializer.record_size
        self.rpp        = PAGE_SIZE // self.rsize      # records per page
        # usar count explícito cuando se provee evita leer slots zero-padded al final
        self.total      = (
            total_records if total_records is not None
            else os.path.getsize(path) // self.rsize
        )
        self._pos       = 0                            # next logical record index
        self._page_id   = -1
        self._page_buf: bytes = b""

    # retorna true si quedan records sin consumir
    def has_next(self) -> bool:
        return self._pos < self.total

    # lee el siguiente record, carga nueva page solo cuando cruza boundary
    def read_next(self) -> Dict[str, Any]:
        pid  = self._pos // self.rpp
        slot = self._pos % self.rpp
        if pid != self._page_id:
            self._page_buf = self.disk.read_block(self.path, pid)
            self._page_id  = pid
        off = slot * self.rsize
        raw = bytes(self._page_buf[off: off + self.rsize])
        self._pos += 1
        return self.serializer.deserialize(raw)


# TPMMS sorter: pass 1 genera runs en disco, pass 2 hace merge k-vías con un heap
class ExternalSorter:

    # configura parámetros del sorter y crea DiskController; session id evita colisiones de nombres
    def __init__(
        self,
        serializer: RecordSerializer,
        key_field: str,
        work_dir: str,
        run_size_pages: int = 10,
    ):
        self.serializer      = serializer
        self.key_field       = key_field
        self.work_dir        = work_dir
        self.run_size_pages  = run_size_pages
        self.record_size     = serializer.record_size
        self.records_per_page = PAGE_SIZE // self.record_size
        self.disk            = DiskController()
        self._session        = os.getpid()   

    # fase 1 del TPMMS: lee input en chunks, ordena en memoria, escribe run files en disco
    def generate_runs(
        self,
        input_path: str,
        run_size_pages: int,
        total_records: int = None,
    ) -> List[Tuple[str, int]]:
        rpp     = self.records_per_page
        buf_cap = run_size_pages * rpp

        reader    = _RunReader(input_path, self.serializer, self.disk, total_records)
        run_info: List[Tuple[str, int]] = []
        run_idx   = 0

        while reader.has_next():
            # llena buffer en memoria hasta buf_cap records
            buf: List[Dict] = []
            while reader.has_next() and len(buf) < buf_cap:
                buf.append(reader.read_next())

            buf.sort(key=lambda r: r[self.key_field])

            # escribe el run ordenado page por page via DiskController para que Telemetry cuente
            run_path  = os.path.join(
                self.work_dir, f"_run_{self._session}_{run_idx}.bin"
            )
            n_run_pages = (len(buf) + rpp - 1) // rpp
            for page_num in range(n_run_pages):
                page  = bytearray(PAGE_SIZE)
                start = page_num * rpp
                end   = min(start + rpp, len(buf))
                for slot, rec in enumerate(buf[start:end]):
                    off = slot * self.record_size
                    page[off: off + self.record_size] = (
                        self.serializer.serialize(rec)
                    )
                self.disk.write_block(run_path, page_num, bytes(page))

            run_info.append((run_path, len(buf)))
            run_idx += 1

        return run_info

    # fase 2 del TPMMS: merge k-vías con min-heap, solo una page por run en RAM a la vez
    def multiway_merge(
        self,
        run_info: List[Tuple[str, int]],
        output_path: str,
    ) -> int:
        readers = [
            _RunReader(rpath, self.serializer, self.disk, cnt)
            for rpath, cnt in run_info
        ]

        # heap contiene tuplas (sort_key, run_index, record_dict) para desempate 
        heap: List[Tuple[Any, int, Dict]] = []
        for i, rdr in enumerate(readers):
            if rdr.has_next():
                rec = rdr.read_next()
                heapq.heappush(heap, (rec[self.key_field], i, rec))

        out_buf   = bytearray(PAGE_SIZE)
        out_slot  = 0
        out_block = 0
        total     = 0
        rpp       = self.records_per_page

        while heap:
            _, run_idx, rec = heapq.heappop(heap)

            # serializa el record en el slot correcto del page buffer de salida
            off = out_slot * self.record_size
            out_buf[off: off + self.record_size] = self.serializer.serialize(rec)
            out_slot += 1
            total    += 1

            if out_slot >= rpp:
                self.disk.write_block(output_path, out_block, bytes(out_buf))
                out_buf   = bytearray(PAGE_SIZE)
                out_slot  = 0
                out_block += 1

            # repone en el heap el siguiente record del run que acaba de contribuir el mínimo
            rdr = readers[run_idx]
            if rdr.has_next():
                nxt = rdr.read_next()
                heapq.heappush(heap, (nxt[self.key_field], run_idx, nxt))

        # flush del último page parcial si quedaron records sin escribir
        if out_slot > 0:
            self.disk.write_block(output_path, out_block, bytes(out_buf))

        return total

    # pipeline completo TPMMS: genera runs, merge, limpia temporales, retorna stats de I/O
    def sort_file(
        self,
        input_path: str,
        output_path: str,
        run_size_pages: int = None,
        total_records: int = None,
    ) -> Tuple[int, int, Dict[str, int]]:
        from core.metrics import Telemetry
        tel = Telemetry()
        rsz = run_size_pages or self.run_size_pages

        r0, w0   = tel.pages_read, tel.pages_written
        run_info = self.generate_runs(input_path, rsz, total_records)
        r1, w1   = tel.pages_read, tel.pages_written

        total = self.multiway_merge(run_info, output_path)
        r2, w2  = tel.pages_read, tel.pages_written

        n_runs = len(run_info)
        for rpath, _ in run_info:
            if os.path.exists(rpath):
                os.remove(rpath)

        stats = {
            "gen_reads":    r1 - r0,
            "gen_writes":   w1 - w0,
            "merge_reads":  r2 - r1,
            "merge_writes": w2 - w1,
            "n_runs":       n_runs,
        }
        return total, n_runs, stats

    # serializa lista de records a flat binary file page por page, retorna count
    def records_to_flat_file(
        self, records: List[Dict[str, Any]], path: str
    ) -> int:
        rpp     = self.records_per_page
        n_pages = (len(records) + rpp - 1) // rpp
        for page_num in range(n_pages):
            page  = bytearray(PAGE_SIZE)
            start = page_num * rpp
            end   = min(start + rpp, len(records))
            for slot, rec in enumerate(records[start:end]):
                off = slot * self.record_size
                page[off: off + self.record_size] = self.serializer.serialize(rec)
            self.disk.write_block(path, page_num, bytes(page))
        return len(records)

    # yield de todos los records de un sorted flat file, una page a la vez
    def scan_sorted_file(
        self, path: str, total_records: int = None
    ) -> Iterator[Dict[str, Any]]:
        rdr = _RunReader(path, self.serializer, self.disk, total_records)
        while rdr.has_next():
            yield rdr.read_next()
