import threading
import time
import functools
# singleton que acumula metricas globales de I/O y tiempo
class Telemetry:
    _instance = None
    _create_lock = threading.Lock()   
   # Implementacion thread-safe de Singleton usando bloqueo de doble check
    def __new__(cls):
        if cls._instance is None:
            with cls._create_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst.pages_read    = 0
                    inst.pages_written = 0
                    inst._total_ms     = 0.0
                    inst._disk_accesses = 0
                    inst._mu           = threading.Lock()
                    cls._instance      = inst
        return cls._instance
    # incrementa pages read 
    def inc_read(self) -> None:
        with self._mu:
            self.pages_read += 1
    # incrementa pages written 
    def inc_write(self) -> None:
        with self._mu:
            self.pages_written += 1
    # resetea todos los contadores
    def reset(self) -> None:
        with self._mu:
            self.pages_read     = 0
            self.pages_written  = 0
            self._total_ms      = 0.0
            self._disk_accesses = 0
    # repr legible para debug con todas las metricas acumuladas
    def __repr__(self):
        return (
            f"Telemetry(reads={self.pages_read}, writes={self.pages_written}, "
            f"disk_accesses={self._disk_accesses}, elapsed_ms={self._total_ms:.3f})"
        )
# Decorador que mide el tiempo transcurrido y contabiliza los accesos netos a disco de cada operacion
def track_performance(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        tel = Telemetry()
        # Captura previa para calcular cuantos accesos a disco genero la operacion
        accesses_before = tel.pages_read + tel.pages_written
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        accesses_after = tel.pages_read + tel.pages_written
        tel._total_ms += elapsed_ms
        tel._disk_accesses += accesses_after - accesses_before
        return result
    return wrapper
