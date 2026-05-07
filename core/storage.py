import os
from core.metrics import Telemetry, track_performance
from core.concurrency import page_lock_manager

PAGE_SIZE = 4096
# controller de bajo nivel para leer y escribir blocks en disco
class DiskController:
    # adquiere shared lock, lee y libera lo que da lugar a readers simultaneos permitidos
    @track_performance
    def read_block(self, file_path: str, block_id: int) -> bytes:
        with page_lock_manager.shared(file_path, block_id):
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            with open(file_path, "rb") as f:
                f.seek(block_id * PAGE_SIZE)
                data = f.read(PAGE_SIZE)
            Telemetry().inc_read()
            return data
    # adquiere exclusive lock luego escribe y luego libera. Esto bloquea readers y otros writers
    @track_performance
    def write_block(self, file_path: str, block_id: int, data: bytes) -> None:
        with page_lock_manager.exclusive(file_path, block_id):
            if len(data) > PAGE_SIZE:
                raise ValueError(f"Data exceeds PAGE_SIZE ({PAGE_SIZE}): got {len(data)} bytes")
            padded = data.ljust(PAGE_SIZE, b"\x00")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            # abre en r+b si el archivo ya existe, sino lo crea con w+b
            mode = "r+b" if os.path.exists(file_path) else "w+b"
            with open(file_path, mode) as f:
                f.seek(block_id * PAGE_SIZE)
                f.write(padded)
            Telemetry().inc_write()
