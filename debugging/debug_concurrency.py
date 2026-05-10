import os
import sys
import tempfile
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.concurrency import (
    init_concurrency, table_lock, atomic_transaction,
    PageRWLock, PageLockManager, WaitForGraph, DeadlockError,
)

def test_init_and_write_journal():
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "journal.log")
        init_concurrency(log_path)
        assert os.path.exists(log_path), "journal.log no fue creado"
        assert os.path.getsize(log_path) == 0, "journal.log no esta vacio al inicio"
    print("  [OK] test_init_and_write_journal")


def test_table_lock_acquire_release():
    with tempfile.TemporaryDirectory() as td:
        init_concurrency(os.path.join(td, "j.log"))
        table_lock.acquire_write("TablaTest", txn_id="test0001")
        assert table_lock.is_write_locked("TablaTest"), "Lock deberia estar tomado"
        table_lock.release_write("TablaTest")
        assert not table_lock.is_write_locked("TablaTest"), "Lock deberia estar libre"
    print("  [OK] test_table_lock_acquire_release")


def test_write_guard_context_manager():
    with tempfile.TemporaryDirectory() as td:
        init_concurrency(os.path.join(td, "j.log"))
        with table_lock.write_guard("GuardTest", txn_id="test0002"):
            assert table_lock.is_write_locked("GuardTest")
        assert not table_lock.is_write_locked("GuardTest")
    print("  [OK] test_write_guard_context_manager")


def test_concurrent_write_detection():
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "j.log")
        init_concurrency(log_path)

        results = []

        def worker(txn_id, delay_before_release):
            table_lock.acquire_write("ConcTest", txn_id=txn_id)
            time.sleep(delay_before_release)
            table_lock.release_write("ConcTest")
            results.append(txn_id)

        t1 = threading.Thread(target=worker, args=("txn_A", 0.05))
        t2 = threading.Thread(target=worker, args=("txn_B", 0.01))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join(); t2.join()

        assert len(results) == 2, "Ambos threads deben completar"
        # verificar que el journal tiene entradas
        with open(log_path) as f:
            content = f.read()
        assert len(content) > 0, "journal.log debe tener entradas"
    print("  [OK] test_concurrent_write_detection")

def test_atomic_transaction_decorator():
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "j.log")
        init_concurrency(log_path)

        class FakeNode:
            table_name = "FakeTable"

        @atomic_transaction
        def fake_op(self, node):
            return "ok"

        class FakeEngine:
            pass

        result = fake_op(FakeEngine(), FakeNode())
        assert result == "ok"

        with open(log_path) as f:
            content = f.read()
        assert "BEGIN" in content
        assert "COMMIT" in content
    print("  [OK] test_atomic_transaction_decorator")


def test_atomic_transaction_rollback_on_error():
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "j.log")
        init_concurrency(log_path)

        class FakeNode:
            table_name = "ErrorTable"

        @atomic_transaction
        def failing_op(self, node):
            raise RuntimeError("error simulado")

        try:
            failing_op(object(), FakeNode())
        except RuntimeError:
            pass

        with open(log_path) as f:
            content = f.read()
        assert "ROLLBACK" in content, "ROLLBACK debe estar en el journal"
    print("  [OK] test_atomic_transaction_rollback_on_error")


# ===========================================================================
# Shared o Exclusive locks + Deadlock Detection 
# ===========================================================================

def _init_tmp_log():
    """Helper: crea tmpdir con journal.log y lo inicializa. Retorna (tmpdir_obj, log_path)."""
    td = tempfile.mkdtemp()
    log_path = os.path.join(td, "j.log")
    init_concurrency(log_path)
    return td, log_path


def test_page_rwlock_multiple_readers():
    """Múltiples threads adquieren shared lock simultáneamente."""
    td, _ = _init_tmp_log()
    lock = PageRWLock()
    acquired = []
    barrier = threading.Barrier(4)

    def reader(idx):
        lock.acquire_read(txn_id=f"r{idx}", page_label="pg1")
        acquired.append(idx)
        barrier.wait()          # todos dentro antes de salir
        lock.release_read()

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(acquired) == 4, f"Solo {len(acquired)}/4 readers adquirieron lock simultáneo"
    import shutil; shutil.rmtree(td, ignore_errors=True)
    print("  [OK] test_page_rwlock_multiple_readers")


def test_page_rwlock_writer_excludes_readers():
    """Writer bloquea a readers nuevos; reader espera hasta que writer libere."""
    td, _ = _init_tmp_log()
    lock = PageRWLock()
    events = []
    writer_holding = threading.Event()

    def writer():
        lock.acquire_write(txn_id="w1", page_label="pg2")
        events.append("write_start")
        writer_holding.set()    # señal: writer ya tiene el lock
        time.sleep(0.08)
        events.append("write_end")
        lock.release_write()

    def reader():
        writer_holding.wait()   # espera hasta que writer tenga el lock
        lock.acquire_read(txn_id="r1", page_label="pg2")
        events.append("read_after_write")
        lock.release_read()

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start(); r.start()
    w.join(timeout=5); r.join(timeout=5)

    assert "write_end" in events and "read_after_write" in events, \
        f"Eventos faltantes: {events}"
    idx_end  = events.index("write_end")
    idx_read = events.index("read_after_write")
    assert idx_read > idx_end, f"Reader entró antes de que writer terminara: {events}"
    import shutil; shutil.rmtree(td, ignore_errors=True)
    print("  [OK] test_page_rwlock_writer_excludes_readers")


def test_page_rwlock_reentrant_read():
    """Misma thread puede adquirir shared lock dos veces sin deadlock."""
    td, _ = _init_tmp_log()
    lock = PageRWLock()
    lock.acquire_read(txn_id="re1", page_label="pg3")
    lock.acquire_read(txn_id="re1", page_label="pg3")   # re-entrant
    lock.release_read()
    lock.release_read()
    import shutil; shutil.rmtree(td, ignore_errors=True)
    print("  [OK] test_page_rwlock_reentrant_read")


def test_page_rwlock_exclusive_context_manager():
    """_PageLockCtx adquiere y libera exclusive lock correctamente."""
    td, _ = _init_tmp_log()
    mgr = PageLockManager()
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        path = f.name
    with mgr.exclusive(path, 0, txn_id="ex1"):
        got = mgr._get(path, 0)._writer
        assert got == threading.get_ident(), "Writer no registrado en lock"
    assert mgr._get(path, 0)._writer is None, "Writer no liberado al salir del ctx"
    os.unlink(path)
    import shutil; shutil.rmtree(td, ignore_errors=True)
    print("  [OK] test_page_rwlock_exclusive_context_manager")


def test_page_lock_manager_shared_context_manager():
    """PageLockManager.shared() permite múltiples lectores simultáneos."""
    td, _ = _init_tmp_log()
    mgr = PageLockManager()
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        path = f.name
    results = []
    barrier = threading.Barrier(3)

    def shared_reader(idx):
        with mgr.shared(path, 0, txn_id=f"s{idx}"):
            results.append(idx)
            barrier.wait()   # todos dentro antes de salir

    threads = [threading.Thread(target=shared_reader, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(results) == 3, "No todos los readers entraron simultáneamente"
    os.unlink(path)
    import shutil; shutil.rmtree(td, ignore_errors=True)
    print("  [OK] test_page_lock_manager_shared_context_manager")


def test_wait_for_graph_no_cycle():
    """WaitForGraph no detecta ciclo cuando no hay uno."""
    g = WaitForGraph()
    # T1 espera T2, T2 espera T3 — no hay ciclo de regreso a T1
    g.register_wait(1, {2})
    g.register_wait(2, {3})
    g.clear_wait(1)
    g.clear_wait(2)
    print("  [OK] test_wait_for_graph_no_cycle")


def test_wait_for_graph_cycle_raises():
    """WaitForGraph detecta ciclo T1->T2->T1 y lanza DeadlockError."""
    g = WaitForGraph()
    g.register_wait(10, {20})   # T10 espera T20
    g.register_wait(20, {30})   # T20 espera T30
    try:
        # T30 espera T10 -> ciclo 30->10->20->30
        g.register_wait(30, {10})
        assert False, "DeadlockError no fue lanzado"
    except DeadlockError:
        pass
    g.clear_wait(10)
    g.clear_wait(20)
    print("  [OK] test_wait_for_graph_cycle_raises")


def test_deadlock_detection_two_threads():
    """
    Escenario clásico de deadlock entre dos threads:
      T1 tiene X(page_A) y pide X(page_B)
      T2 tiene X(page_B) y pide X(page_A)
    El thread que llega segundo detecta el ciclo y lanza DeadlockError.
    """
    td, _ = _init_tmp_log()
    lock_a = PageRWLock()
    lock_b = PageRWLock()
    errors = []
    t1_has_a = threading.Event()
    t2_has_b = threading.Event()

    def thread1():
        lock_a.acquire_write(txn_id="t1", page_label="A")
        t1_has_a.set()
        t2_has_b.wait()         # espera que T2 tenga lock_b
        try:
            lock_b.acquire_write(txn_id="t1", page_label="B")
            lock_b.release_write()
        except DeadlockError as e:
            errors.append(("t1", str(e)))
        finally:
            lock_a.release_write()

    def thread2():
        lock_b.acquire_write(txn_id="t2", page_label="B")
        t2_has_b.set()
        t1_has_a.wait()         # espera que T1 tenga lock_a
        try:
            lock_a.acquire_write(txn_id="t2", page_label="A")
            lock_a.release_write()
        except DeadlockError as e:
            errors.append(("t2", str(e)))
        finally:
            lock_b.release_write()

    t1 = threading.Thread(target=thread1)
    t2 = threading.Thread(target=thread2)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    assert len(errors) >= 1, "Deadlock no fue detectado"
    import shutil; shutil.rmtree(td, ignore_errors=True)
    print(f"  [OK] test_deadlock_detection_two_threads  (detectado en {errors[0][0]})")


def test_disk_controller_uses_page_locks():
    """DiskController.read_block usa shared lock; write_block usa exclusive lock."""
    from core.storage import DiskController
    from core.concurrency import page_lock_manager

    with tempfile.TemporaryDirectory() as td:
        init_concurrency(os.path.join(td, "j.log"))
        path = os.path.join(td, "test.bin")
        dc   = DiskController()
        data = b"hello jupiter"
        dc.write_block(path, 0, data)

        read = dc.read_block(path, 0)
        assert read[:len(data)] == data, "Datos leídos no coinciden"

        key = (path, 0)
        assert key in page_lock_manager._locks, "Lock de página no registrado en manager"
    print("  [OK] test_disk_controller_uses_page_locks")

if __name__ == "__main__":
    print("=== debug_concurrency.py ===")
    test_init_and_write_journal()
    test_table_lock_acquire_release()
    test_write_guard_context_manager()
    test_concurrent_write_detection()
    test_atomic_transaction_decorator()
    test_atomic_transaction_rollback_on_error()

    print()
    print("--- Shared/Exclusive Locks + Deadlock Detection (+2 pts) ---")
    test_page_rwlock_multiple_readers()
    test_page_rwlock_writer_excludes_readers()
    test_page_rwlock_reentrant_read()
    test_page_rwlock_exclusive_context_manager()
    test_page_lock_manager_shared_context_manager()
    test_wait_for_graph_no_cycle()
    test_wait_for_graph_cycle_raises()
    test_deadlock_detection_two_threads()
    test_disk_controller_uses_page_locks()

    print()
    print("Todos los tests pasaron.")
