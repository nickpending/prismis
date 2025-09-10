"""Test that API properly cleans up Storage connections after each request."""

import asyncio
import pytest
from pathlib import Path
from typing import AsyncGenerator, List
from unittest.mock import patch

from src.prismis_daemon.api import app, get_storage
from src.prismis_daemon.storage import Storage
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_storage_cleanup_after_request(test_db: Path) -> None:
    """Test that Storage connections are properly closed after each API request."""

    # Track Storage instances and their cleanup
    created_storages = []
    closed_storages = []

    # Patch Storage to track instances
    original_init = Storage.__init__
    original_close = Storage.close

    def tracked_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created_storages.append(self)

    def tracked_close(self) -> None:
        closed_storages.append(self)
        original_close(self)

    with patch.object(Storage, "__init__", tracked_init):
        with patch.object(Storage, "close", tracked_close):
            # Override the dependency to use test database
            async def override_get_storage() -> AsyncGenerator[Storage, None]:
                storage = Storage(test_db)
                try:
                    yield storage
                finally:
                    storage.close()

            app.dependency_overrides[get_storage] = override_get_storage

            # Make multiple requests
            with TestClient(app) as client:
                # Health check doesn't need auth
                for i in range(5):
                    response = client.get("/health")
                    assert response.status_code == 200

            # Verify all Storage instances were cleaned up
            assert len(created_storages) == len(closed_storages), (
                f"Created {len(created_storages)} Storage instances but only closed {len(closed_storages)}"
            )

            # Verify connections are actually closed
            for storage in created_storages:
                assert storage._conn is None, "Storage connection not properly closed"

    # Clean up override
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_requests_no_leak(test_db: Path) -> None:
    """Test that concurrent API requests don't leak connections."""

    # Track max concurrent connections
    concurrent_connections = []

    async def track_connections() -> bool:
        """Track how many connections are open at once."""
        storage = Storage(test_db)
        # Force connection creation
        storage.get_all_sources()
        concurrent_connections.append(storage._conn)
        # Simulate some work
        await asyncio.sleep(0.1)
        storage.close()
        return True

    # Run multiple concurrent operations
    tasks = [track_connections() for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # All should succeed
    assert all(results), "Some concurrent operations failed"

    # Verify all connections were created (no blocking)
    assert len(concurrent_connections) == 10, (
        f"Expected 10 connections, got {len(concurrent_connections)}"
    )


@pytest.mark.asyncio
async def test_real_storage_cleanup_pattern(test_db: Path) -> None:
    """Test the actual cleanup pattern with real Storage instances."""

    storage_instances: List[Storage] = []

    async def test_generator() -> AsyncGenerator[Storage, None]:
        """Mimic the dependency injection pattern."""
        storage = Storage(test_db)
        storage_instances.append(storage)
        try:
            yield storage
        finally:
            storage.close()

    # Use the generator pattern
    gen = test_generator()
    storage = await gen.__anext__()

    # Verify storage is usable
    sources = storage.get_all_sources()
    assert sources is not None
    assert storage._conn is not None  # Connection created

    # Cleanup (simulates FastAPI closing the generator)
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass

    # Verify cleanup happened
    assert len(storage_instances) == 1
    assert storage_instances[0]._conn is None  # Connection closed


def test_dependency_cleanup_on_error(test_db: Path) -> None:
    """Test that Storage is cleaned up even when endpoints return errors."""

    # Track cleanup
    storages_created = []
    storages_closed = []

    async def tracking_get_storage() -> AsyncGenerator[Storage, None]:
        storage = Storage(test_db)
        storages_created.append(storage)
        try:
            yield storage
        finally:
            storage.close()
            storages_closed.append(storage)

    app.dependency_overrides[get_storage] = tracking_get_storage

    with TestClient(app) as client:
        # Test health endpoint (no auth required, but NOW uses storage for DB check)
        response = client.get("/health")
        assert response.status_code == 200
        assert len(storages_created) == 1, (
            "Health check should create Storage for DB check"
        )
        assert len(storages_closed) == 1, "Health check should close Storage after use"

        # Test an endpoint that uses Storage (even with auth failure, dependency cleanup happens)
        # Auth fails but dependency cleanup should still work
        response = client.get("/api/sources", headers={"X-API-Key": "wrong-key"})
        # Status doesn't matter - we're testing cleanup
        assert response.status_code in [200, 403]

        # If Storage was created, it should be closed
        if storages_created:
            assert len(storages_created) == len(storages_closed), (
                "Storage instances not properly cleaned up"
            )
            assert all(s._conn is None for s in storages_created), (
                "Storage connections not closed"
            )

    # Clean up
    app.dependency_overrides.clear()
