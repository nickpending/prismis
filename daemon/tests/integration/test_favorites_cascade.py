"""Integration tests for favorites cascade deletion behavior - critical invariants."""

import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from prismis_daemon.storage import Storage
from prismis_daemon.models import ContentItem
from fastapi.testclient import TestClient
from prismis_daemon.api import app


def test_favorites_survive_source_deletion(test_db: Path) -> None:
    """
    INVARIANT: Favorited content persists after source deletion
    BREAKS: User loses curated content - catastrophic data loss
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add multiple content items
    items = []
    for i in range(5):
        item = ContentItem(
            source_id=source_id,
            external_id=f"test-{i}",
            title=f"Article {i}",
            url=f"https://example.com/{i}",
            content=f"Content {i}",
            summary=f"Summary {i}",
            priority="high" if i < 2 else "medium",
            published_at=datetime.now(),
        )
        content_id = storage.add_content(item)
        items.append(content_id)

    # Mark some as favorites, some as read
    storage.update_content_status(items[0], favorited=True)
    storage.update_content_status(items[1], favorited=True, read=True)
    storage.update_content_status(items[2], read=True)  # Read but NOT favorited

    # Delete the source
    success = storage.remove_source(source_id)
    assert success, "Failed to remove source"

    # INVARIANT: Favorited content MUST survive
    fav1 = storage.get_content_by_id(items[0])
    fav2 = storage.get_content_by_id(items[1])
    assert fav1 is not None, "Favorited content was deleted!"
    assert fav2 is not None, "Favorited+read content was deleted!"
    assert fav1["favorited"] is True
    assert fav2["favorited"] is True
    assert fav1["source_id"] is None, "Source ID should be NULL for orphaned favorite"
    assert fav2["source_id"] is None, "Source ID should be NULL for orphaned favorite"

    # Content still has all its data intact
    assert fav1["title"] == "Article 0"
    assert fav1["content"] == "Content 0"
    assert fav1["priority"] == "high"


def test_nonfavorites_deleted_with_source(test_db: Path) -> None:
    """
    INVARIANT: Non-favorited content is deleted with source
    BREAKS: Database fills with orphaned content
    """
    storage = Storage(test_db)

    # Add source and content
    source_id = storage.add_source("https://example.com", "rss", "Test")

    # Add mix of favorited and non-favorited content
    items = []
    for i in range(4):
        item = ContentItem(
            source_id=source_id,
            external_id=f"item-{i}",
            title=f"Article {i}",
            url=f"https://example.com/{i}",
            content=f"Content {i}",
            published_at=datetime.now(),
        )
        content_id = storage.add_content(item)
        items.append(content_id)

    # Only mark one as favorite
    storage.update_content_status(items[0], favorited=True)
    storage.update_content_status(items[1], read=True)  # Read but not favorited

    # Remove source
    storage.remove_source(source_id)

    # INVARIANT: Non-favorited content MUST be deleted
    assert storage.get_content_by_id(items[1]) is None, (
        "Read non-favorite should be deleted"
    )
    assert storage.get_content_by_id(items[2]) is None, (
        "Unread non-favorite should be deleted"
    )
    assert storage.get_content_by_id(items[3]) is None, (
        "Unread non-favorite should be deleted"
    )

    # But favorite survives
    assert storage.get_content_by_id(items[0]) is not None, "Favorite should survive"


def test_concurrent_favorite_during_delete(test_db: Path) -> None:
    """
    INVARIANT: Concurrent operations safe during deletion
    BREAKS: Race condition causes data corruption or loss
    """
    storage = Storage(test_db)

    # Add source with content
    source_id = storage.add_source("https://example.com", "rss", "Test")

    items = []
    for i in range(10):
        item = ContentItem(
            source_id=source_id,
            external_id=f"race-{i}",
            title=f"Article {i}",
            url=f"https://example.com/{i}",
            content=f"Content {i}",
            published_at=datetime.now(),
        )
        content_id = storage.add_content(item)
        items.append(content_id)

    # Track which items we're trying to favorite
    items_to_favorite = items[:5]
    deletion_started = threading.Event()
    favoriting_done = threading.Event()

    def try_to_favorite() -> None:
        """Try to favorite items while deletion is happening."""
        deletion_started.wait()  # Wait for deletion to start
        storage_instance = Storage(test_db)
        for content_id in items_to_favorite:
            try:
                storage_instance.update_content_status(content_id, favorited=True)
            except Exception:
                pass  # Some might fail if already deleted
        favoriting_done.set()

    def delete_source_slowly() -> None:
        """Delete source with a slight delay to allow race."""
        storage_instance = Storage(test_db)
        deletion_started.set()
        # Small delay to let favoriting start
        import time

        time.sleep(0.01)
        storage_instance.remove_source(source_id)

    # Run concurrent operations
    with ThreadPoolExecutor(max_workers=2) as executor:
        fav_future = executor.submit(try_to_favorite)
        del_future = executor.submit(delete_source_slowly)

        # Wait for both to complete
        fav_future.result()
        del_future.result()

    # INVARIANT: No data corruption occurred
    # Check all items - they should either be:
    # 1. Deleted (non-favorited)
    # 2. Present with favorited=True and source_id=NULL
    for content_id in items:
        content = storage.get_content_by_id(content_id)
        if content is not None:
            # If it exists, it MUST be favorited with NULL source
            assert content["favorited"] is True, "Surviving content must be favorited"
            assert content["source_id"] is None, (
                "Surviving content must have NULL source_id"
            )
            # Data integrity check
            assert "Article" in content["title"], "Content data corrupted"
            assert content["content"] is not None, "Content body lost"


### CHECKPOINT 7: Implement Failure Mode Tests


def test_simultaneous_source_deletions(test_db: Path) -> None:
    """
    FAILURE: Multiple clients delete same source simultaneously
    GRACEFUL: Operation is idempotent, no corruption
    """
    storage = Storage(test_db)

    # Add source with favorited content
    source_id = storage.add_source("https://example.com", "rss", "Test")

    item = ContentItem(
        source_id=source_id,
        external_id="concurrent-delete",
        title="Important Article",
        url="https://example.com/1",
        content="Important content",
        published_at=datetime.now(),
    )
    content_id = storage.add_content(item)
    storage.update_content_status(content_id, favorited=True)

    # Simulate multiple clients trying to delete the same source
    def attempt_delete(client_num: int) -> tuple[int, bool]:
        """Each client attempts deletion."""
        storage_instance = Storage(test_db)
        try:
            result = storage_instance.remove_source(source_id)
            return (client_num, result)
        except Exception:
            return (client_num, False)

    # Run 5 concurrent deletion attempts
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(attempt_delete, i) for i in range(5)]
        results = [f.result() for f in as_completed(futures)]

    # GRACEFUL DEGRADATION:
    # - At least one should succeed (the first one)
    # - Others should fail gracefully (source already gone)
    # - No exceptions or corruption
    successes = [r for r in results if r[1]]
    assert len(successes) >= 1, "At least one delete should succeed"

    # Favorite content should still be preserved correctly
    content = storage.get_content_by_id(content_id)
    assert content is not None, "Favorited content should survive"
    assert content["favorited"] is True
    assert content["source_id"] is None

    # Source should be gone
    sources = storage.get_all_sources()
    assert not any(s["id"] == source_id for s in sources), "Source should be deleted"


def test_api_respects_favorites_preservation(test_db: Path) -> None:
    """
    FAILURE: API cascade behavior doesn't match storage layer
    GRACEFUL: API and storage layer have consistent behavior
    """
    storage = Storage(test_db)
    api_client = TestClient(app)

    # Add source via API
    response = api_client.post(
        "/api/sources",
        json={
            "url": "https://simonwillison.net/atom/everything/",
            "type": "rss",
            "name": "Test Feed",
        },
        headers={"X-API-Key": "prismis-api-4d5e"},
    )
    assert response.status_code == 200
    source_id = response.json()["data"]["id"]

    # Add content directly (simulating daemon fetching)
    items = []
    for i in range(3):
        item = ContentItem(
            source_id=source_id,
            external_id=f"api-test-{i}",
            title=f"Article {i}",
            url=f"https://example.com/{i}",
            content=f"Content {i}",
            priority="high",
            published_at=datetime.now(),
        )
        content_id = storage.add_content(item)
        items.append(content_id)

    # Mark one as favorite
    storage.update_content_status(items[0], favorited=True)
    storage.update_content_status(items[1], read=True)  # Read but not favorited

    # Delete source via API
    response = api_client.delete(
        f"/api/sources/{source_id}", headers={"X-API-Key": "prismis-api-4d5e"}
    )
    assert response.status_code == 200

    # INVARIANT: API deletion must preserve favorites just like direct storage
    fav_content = storage.get_content_by_id(items[0])
    assert fav_content is not None, "API delete should preserve favorites"
    assert fav_content["favorited"] is True
    assert fav_content["source_id"] is None, "API delete should orphan favorites"

    # Non-favorited should be gone
    assert storage.get_content_by_id(items[1]) is None, (
        "API should delete non-favorites"
    )
    assert storage.get_content_by_id(items[2]) is None, (
        "API should delete non-favorites"
    )

    # Source should be gone
    response = api_client.get("/api/sources", headers={"X-API-Key": "prismis-api-4d5e"})
    sources = response.json()["sources"]
    assert not any(s["id"] == source_id for s in sources), "Source should be deleted"
