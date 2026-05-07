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

if __name__ == "__main__":
    test_init_and_write_journal()
    test_table_lock_acquire_release()
    test_write_guard_context_manager()
    test_concurrent_write_detection()
    print("debug_concurrency.py: todos los tests pasaron")
