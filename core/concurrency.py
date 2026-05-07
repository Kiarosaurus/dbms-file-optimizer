from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from functools import wraps
from typing import Dict, Optional, Set, Tuple

# singletons de modulo: ruta del journal y los locks de escritura/tablas
_log_path: Optional[str] = None
_log_file_lock  = threading.Lock()
_table_locks:    Dict[str, threading.Lock] = {}
_table_meta_lock = threading.Lock()

# inicializa o trunca el archivo de transaction journal
def init_concurrency(log_path: str) -> None:
    global _log_path
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    _log_path = log_path
    open(log_path, "w").close()   # truncate / create

# Escribe el log seguro. Si no arrancaste init_concurrency, no hace nada
def _emit(txn_id: str, status: str, operation: str,
          table: str = "-", detail: str = "") -> None:
    if not _log_path:
        return
    ts  = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    tid = threading.get_ident()
    line = (f"[{ts}] TXN={txn_id}  TID={tid:>6}  {status:<8}  "
            f"{operation:<38}  tbl={table}")
    if detail:
        line += f"  | {detail}"
    with _log_file_lock:
        with open(_log_path, "a") as fh:
            fh.write(line + "\n")

# obtiene o crea el lock exclusivo para una tabla dada de forma thread-safe
def _get_tlock(table: str) -> threading.Lock:
    with _table_meta_lock:
        if table not in _table_locks:
            _table_locks[table] = threading.Lock()
        return _table_locks[table]

# Lock de escritura por tabla. Los reads pasan tranquilos, solo frena si hay dos writes a la vez
class TableLock:

    # intenta adquirir sin bloquear, en caso falle hace WAITING y bloquea 
    def acquire_write(self, table: str, txn_id: str = "--------") -> None:
        lock = _get_tlock(table)
        # Tantea a ver si la tabla esta ocupada sin bloquearse
        got = lock.acquire(blocking=False)
        if got:
            return                 
        _emit(txn_id, "WAITING",
              f"(thread {threading.get_ident()})", table,
              "blocked on write lock")
        lock.acquire()                    
        _emit(txn_id, "RESUMED",
              f"(thread {threading.get_ident()})", table,
              "write lock acquired")

    # libera el write lock de la tabla
    def release_write(self, table: str) -> None:
        _get_tlock(table).release()

    # prueba si la tabla tiene un write lock activo sin bloquearse
    def is_write_locked(self, table: str) -> bool:
        lock = _get_tlock(table)
        got  = lock.acquire(blocking=False)
        if got:
            lock.release()
        return not got

    class _WriteGuard:
        # almacena referencia al tablelock padre y la tabla objetivo
        def __init__(self, tl: "TableLock", table: str, txn_id: str):
            self._tl = tl
            self._table = table
            self._txn_id = txn_id

        def __enter__(self):
            self._tl.acquire_write(self._table, self._txn_id)
            return self

        def __exit__(self, *_):
            self._tl.release_write(self._table)

    # factory que devuelve un context manager para el write lock de una tabla
    def write_guard(self, table: str, txn_id: str = "--------") -> "_WriteGuard":
        return self._WriteGuard(self, table, txn_id)


table_lock = TableLock()


# decorator que asigna un UUID a cada llamada y loguea BEGIN, COMMIT o ROLLBACK con timing
def atomic_transaction(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        txn_id = uuid.uuid4().hex[:8]

        # Trata de sacar el nombre de la tabla del segundo parámetro (InstructionNode)
        table = "-"
        if len(args) > 1:
            node = args[1]
            for attr in ("table_name", "source", "name"):
                val = getattr(node, attr, None)
                if val:
                    table = val
                    break

        _emit(txn_id, "BEGIN", fn.__qualname__, table)
        t0 = time.perf_counter()
        try:
            result  = fn(*args, **kwargs)
            elapsed = (time.perf_counter() - t0) * 1000
            _emit(txn_id, "COMMIT", fn.__qualname__, table, f"{elapsed:.1f}ms")
            return result
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            _emit(txn_id, "ROLLBACK", fn.__qualname__, table, f"ERROR: {exc}")
            raise

    return wrapper


# Locks shared/exclusive por página + Detección de deadlocks (2 puntos extra? Con fe)

class DeadlockError(RuntimeError):
    pass


_LOCK_TIMEOUT = 5.0   


# Wait-for graph — detecta ciclos = deadlock
class WaitForGraph:
    """
    Grafo dirigido de espera entre threads.
    Borde T1 a T2 significa que T1 espera un lock que T2 sostiene.
    Ciclo en el grafo = deadlock.
    """
    def __init__(self) -> None:
        self._mu    = threading.Lock()
        self._edges: Dict[int, Set[int]] = {}   # waiter_tid → holders

    def register_wait(self, waiter: int, holders: Set[int]) -> None:
        """Anota que el waiter se quedo esperando a los holders. Si detecta un ciclo, tira un DeadlockError."""
        with self._mu:
            self._edges[waiter] = holders - {waiter}
            if self._cycle_from(waiter):
                del self._edges[waiter]
                raise DeadlockError(
                    f"Deadlock: thread {waiter} waits on {holders} — cycle in wait-for graph"
                )

    def clear_wait(self, waiter: int) -> None:
        with self._mu:
            self._edges.pop(waiter, None)

    def _cycle_from(self, start: int) -> bool:
        """BFS desde start; retorna True si algun camino regresa a start."""
        visited: Set[int] = set()
        queue = list(self._edges.get(start, []))
        while queue:
            node = queue.pop(0)
            if node == start:
                return True
            if node in visited:
                continue
            visited.add(node)
            queue.extend(self._edges.get(node, []))
        return False


_wait_graph = WaitForGraph()


# Lock de read/write por página. Prioridad a los que escriben para que no se queden colgados para siempre.
class PageRWLock:
    """
    Lock de lectura (shared) y escritura (exclusive) para una página en disco.
    - Varios readers pueden leer a la vez sin chocar.
    - El writer hace cola hasta que la página esté completamente vacía.
    - Pase VIP al writer: si hay un writer esperando, los nuevos readers se aguantan.
    - Conectado al WaitForGraph para detectar deadlocks y no morir en el intento.
    """

    def __init__(self) -> None:
        self._cond              = threading.Condition(threading.Lock())
        self._readers: Dict[int, int] = {}   
        self._writer:  Optional[int]  = None
        self._writer_count: int       = 0  
        self._writers_waiting: int    = 0

    # ── shared (read) ──────────────────────────────────────────────────────

    def acquire_read(self, txn_id: str = "-", page_label: str = "?") -> None:
        tid      = threading.get_ident()
        deadline = time.monotonic() + _LOCK_TIMEOUT

        with self._cond:
            # re-entrant: misma thread ya tiene shared lock
            if self._readers.get(tid, 0) > 0:
                self._readers[tid] += 1
                return

            while self._writer is not None or self._writers_waiting > 0:
                holders = ({self._writer} if self._writer else set()) - {tid}
                if holders:
                    _emit(txn_id, "LOCK_WAIT", f"S({page_label})",
                          detail=f"tid={tid} blocked by {holders}")
                    try:
                        _wait_graph.register_wait(tid, holders)
                    except DeadlockError:
                        _emit(txn_id, "DEADLOCK", f"S({page_label})",
                              detail=f"cycle: tid={tid}")
                        raise

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeadlockError(
                        f"Shared lock timeout on '{page_label}' txn={txn_id} tid={tid}"
                    )
                self._cond.wait(timeout=min(remaining, 0.05))
                _wait_graph.clear_wait(tid)

            self._readers[tid] = 1

    def release_read(self) -> None:
        tid = threading.get_ident()
        with self._cond:
            cnt = self._readers.get(tid, 0)
            if cnt > 1:
                self._readers[tid] = cnt - 1
            else:
                self._readers.pop(tid, None)
            if not self._readers:
                self._cond.notify_all()

    # ── exclusive (write) ──────────────────────────────────────────────────

    def acquire_write(self, txn_id: str = "-", page_label: str = "?") -> None:
        tid      = threading.get_ident()
        deadline = time.monotonic() + _LOCK_TIMEOUT

        with self._cond:
            # Re-entrant: misma thread ya sostiene el write lock
            if self._writer == tid:
                self._writer_count += 1
                return

            self._writers_waiting += 1
            try:
                while self._writer is not None or self._readers:
                    holders = (
                        set(self._readers) | ({self._writer} if self._writer else set())
                    ) - {tid}
                    if holders:
                        _emit(txn_id, "LOCK_WAIT", f"X({page_label})",
                              detail=f"tid={tid} blocked by {holders}")
                        try:
                            _wait_graph.register_wait(tid, holders)
                        except DeadlockError:
                            _emit(txn_id, "DEADLOCK", f"X({page_label})",
                                  detail=f"cycle: tid={tid}")
                            raise

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeadlockError(
                            f"Exclusive lock timeout on '{page_label}' txn={txn_id} tid={tid}"
                        )
                    self._cond.wait(timeout=min(remaining, 0.05))
                    _wait_graph.clear_wait(tid)
            finally:
                self._writers_waiting -= 1
                _wait_graph.clear_wait(tid)

            self._writer       = tid
            self._writer_count = 1

    def release_write(self) -> None:
        with self._cond:
            if self._writer == threading.get_ident():
                self._writer_count -= 1
                if self._writer_count == 0:
                    self._writer = None
                    self._cond.notify_all()



# Context manager wrapper
class _PageLockCtx:
    def __init__(
        self,
        lock: PageRWLock,
        exclusive: bool,
        txn_id: str,
        page_label: str,
    ) -> None:
        self._lock  = lock
        self._ex    = exclusive
        self._tid   = txn_id
        self._label = page_label

    def __enter__(self) -> "_PageLockCtx":
        if self._ex:
            self._lock.acquire_write(self._tid, self._label)
        else:
            self._lock.acquire_read(self._tid, self._label)
        return self

    def __exit__(self, exc_type, *_) -> bool:
        if self._ex:
            self._lock.release_write()
        else:
            self._lock.release_read()
        return False   



# Global page lock registry
class PageLockManager:
    """
    Registro global de PageRWLocks indexados por (file_path, block_id).
    Usado por DiskController para serializar accesos concurrentes por página.
    """

    def __init__(self) -> None:
        self._mu:    threading.Lock                           = threading.Lock()
        self._locks: Dict[Tuple[str, int], PageRWLock]       = {}

    def _get(self, file_path: str, block_id: int) -> PageRWLock:
        key = (file_path, block_id)
        with self._mu:
            if key not in self._locks:
                self._locks[key] = PageRWLock()
            return self._locks[key]

    def shared(
        self, file_path: str, block_id: int, txn_id: str = "-"
    ) -> _PageLockCtx:
        label = f"{os.path.basename(file_path)}#{block_id}"
        return _PageLockCtx(self._get(file_path, block_id),
                            exclusive=False, txn_id=txn_id, page_label=label)

    def exclusive(
        self, file_path: str, block_id: int, txn_id: str = "-"
    ) -> _PageLockCtx:
        label = f"{os.path.basename(file_path)}#{block_id}"
        return _PageLockCtx(self._get(file_path, block_id),
                            exclusive=True, txn_id=txn_id, page_label=label)

    def stats(self) -> str:
        with self._mu:
            return f"PageLockManager: pages_tracked={len(self._locks)}"

page_lock_manager = PageLockManager()
