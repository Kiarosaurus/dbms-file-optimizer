from __future__ import annotations
import os
import struct
import zlib
from typing import Any, Dict, List, Optional, Tuple

from core.storage import DiskController, PAGE_SIZE
from core.schema import RecordSerializer, FieldType
from indexing.base_index import BaseIndex

# ─── Constantes del layout binario ─────────

# Página de bucket: [local_depth:u32][n_records:u32][records...]
BUCKET_HDR_FMT  = "!II"
BUCKET_HDR_SIZE = struct.calcsize(BUCKET_HDR_FMT)   # 8 bytes

# Página de metadata (block 0): solo header — el directorio se guarda en <file>.dir
#   [global_depth:u32][dir_size:u32][next_block:u32]
META_HDR_FMT  = "!III"
META_HDR_SIZE = struct.calcsize(META_HDR_FMT)        # 12 bytes

MAX_GLOBAL_DEPTH = 24  # 2^24 = 16M entradas en el archivo .dir; sin restricción de page size


class ExtendibleHashIndex(BaseIndex):

    # inicializa el índice desde disco o crea un archivo nuevo si no existe
    def __init__(
        self,
        file_path: str,
        serializer: RecordSerializer,
        key_field: str,
        bucket_size: Optional[int] = None,  # override para demos; por defecto llena la page
    ):
        self.file_path   = file_path
        self.serializer  = serializer
        self.key_field   = key_field
        self.record_size = serializer.record_size
        self.disk        = DiskController()

        self.bucket_cap: int = (
            bucket_size
            if bucket_size is not None
            else (PAGE_SIZE - BUCKET_HDR_SIZE) // self.record_size
        )

        # Estado del directorio en memoria, siempre consistente con el block 0
        self.global_depth: int  = 0
        self.directory: List[int] = []
        self._next_block: int   = 0   # block_id más alto asignado hasta ahora

        if not os.path.exists(file_path) or not os.path.exists(file_path + ".dir"):
            self._bootstrap()
        else:
            self._load_meta()

    # ─── API pública ───────

    # hashea la key y elige el bucket usando global_depth bits — acceso a disco O(1)
    def search(self, key: Any) -> Optional[Dict[str, Any]]:
        h         = self._hash(key)
        bucket_id = self.directory[h]
        page      = self._read_page(bucket_id)   # el único acceso a disco
        return self._bucket_scan(page, key)

    # inserta un record, haciendo split y doubling del directorio si hay overflow
    def add(self, record: Dict[str, Any]) -> None:
        while True:
            key       = record[self.key_field]
            h         = self._hash(key)
            bucket_id = self.directory[h]
            page      = self._read_page(bucket_id)
            ld, n     = self._bucket_header(page)

            if n < self.bucket_cap:
                self._bucket_append(page, record, n, ld)
                self._write_page(bucket_id, bytes(page))
                return

            # dobla el directorio cuando local_depth == global_depth
            if ld == self.global_depth:
                if self.global_depth >= MAX_GLOBAL_DEPTH:
                    raise RuntimeError(
                        f"global_depth cap ({MAX_GLOBAL_DEPTH}) reached; "
                        "cannot split — check for hash collisions"
                    )
                self._double_directory()

            self._split_bucket(bucket_id, h, ld)

    # elimina todos los records con la key dada; intenta merge del bucket después del borrado
    def remove(self, key: Any) -> int:
        h         = self._hash(key)
        bucket_id = self.directory[h]
        page      = self._read_page(bucket_id)
        ld, count = self._bucket_header(page)

        kept: List[Dict[str, Any]] = []
        removed = 0
        for i in range(count):
            off = BUCKET_HDR_SIZE + i * self.record_size
            raw = bytes(page[off: off + self.record_size])
            rec = self.serializer.deserialize(raw)
            if rec[self.key_field] == key:
                removed += 1
            else:
                kept.append(rec)

        if removed == 0:
            return 0

        self._write_bucket_records(bucket_id, ld, kept)
        self._try_merge_bucket(bucket_id, h)
        return removed

    def reorganize(self) -> None:
        pass   # extendible hashing se autoorganiza

    # ─── Merge de bucket + reducción del directorio ─────────

    # intenta fusionar bucket_id con su buddy si tienen el mismo local_depth y caben juntos
    def _try_merge_bucket(self, bucket_id: int, h: int) -> None:
        page = self._read_page(bucket_id)
        ld, n = self._bucket_header(page)

        if ld == 0:
            return 

        pattern      = h & ((1 << ld) - 1)
        buddy_pattern = pattern ^ (1 << (ld - 1))

        buddy_id: Optional[int] = None
        for i, bid in enumerate(self.directory):
            if (i & ((1 << ld) - 1)) == buddy_pattern:
                buddy_id = bid
                break

        if buddy_id is None or buddy_id == bucket_id:
            return

        buddy_page = self._read_page(buddy_id)
        buddy_ld, buddy_n = self._bucket_header(buddy_page)

        # Solo fusionar si el buddy tiene el mismo local_depth y los registros caben
        if buddy_ld != ld or n + buddy_n > self.bucket_cap:
            return

        cur_recs   = [self._bucket_rec(page,       i) for i in range(n)]
        buddy_recs = [self._bucket_rec(buddy_page,  i) for i in range(buddy_n)]
        merged     = cur_recs + buddy_recs

        new_ld      = ld - 1
        new_pattern = pattern & ((1 << new_ld) - 1) if new_ld > 0 else 0

        self._write_bucket_records(bucket_id, new_ld, merged)

        # Redirige todas las entradas del directorio con el nuevo prefijo a bucket_id
        mask = (1 << new_ld) - 1 if new_ld > 0 else 0
        for i in range(len(self.directory)):
            if (i & mask) == new_pattern:
                self.directory[i] = bucket_id

        self._save_meta()
        self._try_shrink_directory()

    # reduce global_depth a la mitad si todos los buckets tienen local_depth < global_depth
    def _try_shrink_directory(self) -> None:
        if self.global_depth == 0:
            return
        for bid in set(self.directory):
            page = self._read_page(bid)
            ld, _ = self._bucket_header(page)
            if ld >= self.global_depth:
                return
        half = len(self.directory) // 2
        self.directory = self.directory[:half]
        self.global_depth -= 1
        self._save_meta()
        self._try_shrink_directory()  

    # ─── Función hash ────────────

    # alias público para inspeccionar el valor hash desde afuera
    def hash_func(self, key: Any) -> int:
        return self._hash(key)

    # hash crc32 truncado a depth bits — efecto avalanche consistente entre plataformas
    def _hash(self, key: Any, depth: Optional[int] = None) -> int:
        if depth is None:
            depth = self.global_depth
        if depth == 0:
            return 0
        if isinstance(key, int):
            raw = key.to_bytes(8, "big", signed=True)
        elif isinstance(key, float):
            raw = struct.pack("!f", key)
        else:
            raw = str(key).encode("utf-8")
        # crc32 da buenos bits de avalanche; mask extrae los depth LSBs
        h = zlib.crc32(raw) & 0xFFFFFFFF
        return h & ((1 << depth) - 1)

    # ─── Duplicación del directorio ──────────────

    # dobla el directorio de 2^d a 2^(d+1) preservando el routing de buckets existentes
    def _double_directory(self) -> None:
        old_size = len(self.directory)
        # new_dir[i] = old_dir[i % old_size] mantiene los pointers correctos
        self.directory = [self.directory[i % old_size] for i in range(old_size * 2)]
        self.global_depth += 1
        self._save_meta()

    # ─── Split de bucket ──────────

    # redistribuye los registros entre el bucket viejo y el nuevo usando el bit extra
    def _split_bucket(self, bucket_id: int, h: int, local_depth: int) -> None:
        page = self._read_page(bucket_id)
        _, n = self._bucket_header(page)
        existing = [self._bucket_rec(page, i) for i in range(n)]

        new_depth = local_depth + 1
        new_id    = self._alloc_block()
        # pattern: los lower local_depth bits identifican los directory slots de este bucket
        pattern   = h & ((1 << local_depth) - 1)

        left_recs:  List[Dict] = []
        right_recs: List[Dict] = []
        for rec in existing:
            rh = self._hash(rec[self.key_field], new_depth)
            # el bit en posición local_depth decide si va al bucket izquierdo o derecho
            if (rh >> local_depth) & 1 == 0:
                left_recs.append(rec)
            else:
                right_recs.append(rec)

        self._write_bucket_records(bucket_id, new_depth, left_recs)
        self._write_bucket_records(new_id,    new_depth, right_recs)

        # Actualiza todas las entradas del directorio que pertenecen a esta partición del bucket
        old_mask = (1 << local_depth) - 1   # 0 cuando local_depth==0 -> coincide con todo
        for i in range(len(self.directory)):
            if (i & old_mask) == pattern:
                if (i >> local_depth) & 1 == 0:
                    self.directory[i] = bucket_id
                else:
                    self.directory[i] = new_id

        self._save_meta()

    # ─── Helpers de página de bucket ─────────────

    # lee el header del bucket page y retorna (local_depth, n_records)
    def _bucket_header(self, page: bytearray) -> Tuple[int, int]:
        return struct.unpack_from(BUCKET_HDR_FMT, page, 0)

    # deserializa el record en la posición idx del bucket page
    def _bucket_rec(self, page: bytearray, idx: int) -> Dict[str, Any]:
        off = BUCKET_HDR_SIZE + idx * self.record_size
        return self.serializer.deserialize(bytes(page[off : off + self.record_size]))

    # escanea el bucket linealmente buscando la key
    def _bucket_scan(self, page: bytearray, key: Any) -> Optional[Dict[str, Any]]:
        _, n = self._bucket_header(page)
        for i in range(n):
            rec = self._bucket_rec(page, i)
            if rec[self.key_field] == key:
                return rec
        return None

    # agrega el record en posición n y actualiza el header
    def _bucket_append(
        self, page: bytearray, record: Dict[str, Any], n: int, local_depth: int
    ) -> None:
        off = BUCKET_HDR_SIZE + n * self.record_size
        page[off : off + self.record_size] = self.serializer.serialize(record)
        struct.pack_into(BUCKET_HDR_FMT, page, 0, local_depth, n + 1)

    # serializa una lista de records en una nueva bucket page y la escribe a disco
    def _write_bucket_records(
        self, bucket_id: int, local_depth: int, records: List[Dict[str, Any]]
    ) -> None:
        page = bytearray(PAGE_SIZE)
        struct.pack_into(BUCKET_HDR_FMT, page, 0, local_depth, len(records))
        for i, rec in enumerate(records):
            off = BUCKET_HDR_SIZE + i * self.record_size
            page[off : off + self.record_size] = self.serializer.serialize(rec)
        self._write_page(bucket_id, bytes(page))

    # ─── Metadata + E/S de pages ──────

    # carga el header del block 0 y el directorio del archivo .dir
    def _load_meta(self) -> None:
        raw = bytearray(self.disk.read_block(self.file_path, 0))
        self.global_depth, dir_size, self._next_block = struct.unpack_from(
            META_HDR_FMT, raw, 0
        )
        with open(self.file_path + ".dir", "rb") as f:
            self.directory = list(struct.unpack(f"!{dir_size}I", f.read(dir_size * 4)))

    # persiste el header en block 0 y el directorio completo en el archivo .dir
    def _save_meta(self) -> None:
        page     = bytearray(PAGE_SIZE)
        dir_size = len(self.directory)
        struct.pack_into(META_HDR_FMT, page, 0, self.global_depth, dir_size, self._next_block)
        self.disk.write_block(self.file_path, 0, bytes(page))
        with open(self.file_path + ".dir", "wb") as f:
            f.write(struct.pack(f"!{dir_size}I", *self.directory))

    # reserva el siguiente block_id; el caller escribe el contenido real
    def _alloc_block(self) -> int:
        self._next_block += 1
        return self._next_block

    # lee una page completa desde disco como bytearray mutable
    def _read_page(self, block_id: int) -> bytearray:
        return bytearray(self.disk.read_block(self.file_path, block_id))

    # escribe una page completa a disco
    def _write_page(self, block_id: int, data: bytes) -> None:
        self.disk.write_block(self.file_path, block_id, data)

    # crea la metadata page y un bucket vacío en el block 1 al arrancar desde cero
    def _bootstrap(self) -> None:
        self.global_depth = 0
        self._next_block  = 1
        self.directory    = [1]
        self._save_meta()
        self._write_bucket_records(1, local_depth=0, records=[])

    # ─── Helpers de debug ────────────

    # retorna un string con el estado del directorio y los buckets para debugging
    def directory_state(self) -> str:
        lines = [f"global_depth={self.global_depth}  buckets={self._next_block}"]
        seen: Dict[int, List[int]] = {}
        for i, bid in enumerate(self.directory):
            seen.setdefault(bid, []).append(i)
        for bid, indices in sorted(seen.items()):
            page = self._read_page(bid)
            ld, n = self._bucket_header(page)
            idx_str = ",".join(str(x) for x in indices)
            lines.append(f"  bucket block={bid}  local_depth={ld}  n={n}  dir_slots=[{idx_str}]")
        return "\n".join(lines)
    