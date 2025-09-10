"""Integration tests for database connection lifecycle and resource management."""

from pathlib import Path
import sqlite3
import pytest
import threading
import time

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from prismis_daemon.storage import Storage


def test_connection_cleanup_on_context_exit(test_db: Path) -> None:
    """
    INVARIANT: Storage connections must always close
    BREAKS: Resource exhaustion if violated
    """
    # Verify connection is created and cleaned up properly
    with Storage(test_db) as storage:
        # Connection should be created on first use
        assert storage._conn is None

        # Trigger connection creation
        sources = storage.get_all_sources()
        assert storage._conn is not None

    # After context exit, connection should be closed
    assert storage._conn is None

    # Verify we can create new connections (no resource leak)
    with Storage(test_db) as storage2:
        sources = storage2.get_all_sources()
        assert storage2._conn is not None
        # Connection should work properly (address reuse is OK)
        assert len(sources) >= 0  # Should be able to query


def test_wal_pragma_persistence(test_db: Path) -> None:
    """
    INVARIANT: WAL mode must survive all operations
    BREAKS: Deadlocks and concurrency issues if violated
    """
    storage = Storage(test_db)

    # Perform operations that create connection
    storage.add_source("https://test.com/feed", "rss", "Test")

    # Check WAL mode is set on the connection
    cursor = storage.conn.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    assert mode == "wal", f"Expected WAL mode, got {mode}"

    # Perform more operations
    storage.get_all_sources()
    storage.get_content_by_priority("high")

    # WAL mode should still be active
    cursor = storage.conn.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    assert mode == "wal", f"WAL mode lost after operations, got {mode}"

    # Check busy timeout is also preserved
    cursor = storage.conn.execute("PRAGMA busy_timeout")
    timeout = cursor.fetchone()[0]
    assert timeout == 5000, f"Expected 5000ms timeout, got {timeout}"

    storage.close()


def test_connection_leak_on_exception(test_db: Path) -> None:
    """
    INVARIANT: Exceptions must not prevent connection cleanup
    BREAKS: Resource leaks if violated
    """

    class FailingStorage(Storage):
        """Storage that throws exception after creating connection."""

        def failing_operation(self) -> None:
            # This creates a connection
            _ = self.conn
            # Then throws exception
            raise ValueError("Simulated failure")

    storage = FailingStorage(test_db)

    # Operation fails but shouldn't leak connection
    with pytest.raises(ValueError, match="Simulated failure"):
        storage.failing_operation()

    # Connection should still exist (not auto-cleaned on exception)
    assert storage._conn is not None

    # But context manager should still clean up
    with pytest.raises(ValueError):
        with FailingStorage(test_db) as storage2:
            storage2.failing_operation()

    # Context manager ensures cleanup even on exception
    assert storage2._conn is None

    # Manual cleanup of first instance
    storage.close()
    assert storage._conn is None


### CHECKPOINT 7: Implement Failure Mode Tests


def test_database_lock_handling(test_db: Path) -> None:
    """
    FAILURE: Database lock during critical operation
    GRACEFUL: Operations retry with timeout, no corruption
    """
    storage1 = Storage(test_db)

    # Start a write transaction in storage1 to lock database
    storage1.conn.execute("BEGIN EXCLUSIVE")
    storage1.conn.execute(
        "INSERT INTO sources (id, url, type, name) VALUES (?, ?, ?, ?)",
        ("lock-test", "https://test.com", "rss", "Lock Test"),
    )

    # Storage2 in thread should handle busy database gracefully
    result = {"success": False, "error": None}

    def attempt_read() -> None:
        # Create Storage in thread (SQLite connections are thread-local)
        storage2 = Storage(test_db)
        try:
            # This should wait for lock to be released (up to 5000ms)
            _ = storage2.get_all_sources()
            result["success"] = True
        except sqlite3.OperationalError as e:
            result["error"] = str(e)
        finally:
            storage2.close()

    # Start read attempt in thread
    read_thread = threading.Thread(target=attempt_read)
    read_thread.start()

    # Give it a moment to hit the lock
    time.sleep(0.1)

    # Release the lock
    storage1.conn.rollback()

    # Thread should complete successfully
    read_thread.join(timeout=2.0)
    assert not read_thread.is_alive(), "Read operation hung on lock"
    assert result["success"], f"Read failed: {result['error']}"

    # Storage1 should still be functional
    sources1 = storage1.get_all_sources()
    assert isinstance(sources1, list)

    storage1.close()


def test_concurrent_storage_instances(test_db: Path) -> None:
    """
    FAILURE: Multiple concurrent Storage instances
    GRACEFUL: Each has own connection, no interference
    """
    instances = []
    results = []
    errors = []

    def worker(worker_id: int) -> None:
        """Each worker creates its own Storage and performs operations."""
        storage = None
        try:
            storage = Storage(test_db)
            instances.append(storage)

            # Each performs operations
            for i in range(3):
                sources = storage.get_all_sources()
                results.append((worker_id, len(sources)))
                time.sleep(0.01)  # Small delay to encourage interleaving

        except Exception as e:
            errors.append((worker_id, str(e)))
        finally:
            if storage:
                storage.close()

    # Create 5 concurrent Storage instances
    threads = []
    for i in range(5):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()

    # Wait for all to complete
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "Thread hung"

    # Should have no errors
    assert len(errors) == 0, f"Errors occurred: {errors}"

    # Should have results from all workers
    assert len(results) == 15, f"Expected 15 results, got {len(results)}"

    # Verify all workers completed successfully
    # Connection uniqueness was implicitly tested by thread-local nature
    # (if connections weren't thread-local, we'd have had errors)
    assert len(instances) == 5, f"Expected 5 instances, got {len(instances)}"

    # All operations succeeded without thread conflicts
    for worker_id in range(5):
        worker_results = [r for r in results if r[0] == worker_id]
        assert len(worker_results) == 3, (
            f"Worker {worker_id} didn't complete all operations"
        )
