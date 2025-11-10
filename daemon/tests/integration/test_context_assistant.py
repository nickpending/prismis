"""Integration tests for Context Assistant database layer.

Tests prune protection invariants for flagged and favorited items.
"""

from pathlib import Path

from prismis_daemon.storage import Storage
from prismis_daemon.models import ContentItem


def test_INVARIANT_flagged_items_excluded_from_prune_count(test_db: Path) -> None:
    """
    INVARIANT: Flagged items excluded from count_unprioritized()
    BREAKS: Prune count inaccuracy leads to user confusion
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add 3 unprioritized items
    items = []
    for i in range(3):
        item = ContentItem(
            source_id=source_id,
            external_id=f"unprioritized-{i}",
            title=f"Unprioritized Article {i}",
            url=f"https://example.com/article-{i}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = storage.add_content(item)
        items.append(content_id)

    # Initial count: all 3 unprioritized items
    count_before = storage.count_unprioritized()
    assert count_before == 3, "Should count all 3 unprioritized items initially"

    # Flag one item as interesting
    storage.flag_interesting(items[0])

    # Count should now exclude the flagged item
    count_after = storage.count_unprioritized()
    assert count_after == 2, "Flagged item should be excluded from prune count"

    # Flag a second item
    storage.flag_interesting(items[1])

    # Count should exclude both flagged items
    count_final = storage.count_unprioritized()
    assert count_final == 1, "Both flagged items should be excluded from prune count"


def test_INVARIANT_flagged_items_not_deleted_by_prune(test_db: Path) -> None:
    """
    INVARIANT: delete_unprioritized() must not delete flagged items
    BREAKS: Data loss of items user explicitly saved for context analysis
    CRITICAL: This is the core prune protection invariant
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add 5 unprioritized items
    flagged_items = []
    unflagged_items = []

    for i in range(5):
        item = ContentItem(
            source_id=source_id,
            external_id=f"test-{i}",
            title=f"Article {i}",
            url=f"https://example.com/article-{i}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = storage.add_content(item)

        # Flag first 2 items
        if i < 2:
            storage.flag_interesting(content_id)
            flagged_items.append(content_id)
        else:
            unflagged_items.append(content_id)

    # Verify setup: 5 total items, 2 flagged
    cursor = storage.conn.execute("SELECT COUNT(*) FROM content WHERE priority IS NULL")
    total_count = cursor.fetchone()[0]
    assert total_count == 5, "Should have 5 unprioritized items"

    flagged = storage.get_flagged_items(limit=10)
    assert len(flagged) == 2, "Should have 2 flagged items"

    # Execute prune
    deleted_count = storage.delete_unprioritized()
    assert deleted_count == 3, "Should delete only the 3 unflagged items"

    # Verify flagged items still exist
    cursor = storage.conn.execute("SELECT COUNT(*) FROM content WHERE priority IS NULL")
    remaining_count = cursor.fetchone()[0]
    assert remaining_count == 2, "Only flagged items should remain"

    # Verify the correct items survived
    cursor = storage.conn.execute("SELECT id FROM content")
    remaining_ids = {row[0] for row in cursor.fetchall()}

    for flagged_id in flagged_items:
        assert flagged_id in remaining_ids, (
            f"Flagged item {flagged_id} was incorrectly deleted"
        )

    for unflagged_id in unflagged_items:
        assert unflagged_id not in remaining_ids, (
            f"Unflagged item {unflagged_id} should have been deleted"
        )


def test_INVARIANT_favorited_items_also_protected_from_prune(test_db: Path) -> None:
    """
    INVARIANT: Both favorited AND flagged items excluded from prune
    BREAKS: Trust violation if favorited items get pruned
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add 4 unprioritized items
    favorited_id = None
    flagged_id = None
    both_id = None
    neither_id = None

    for i, case in enumerate(["favorited", "flagged", "both", "neither"]):
        item = ContentItem(
            source_id=source_id,
            external_id=f"test-{case}",
            title=f"Article {case}",
            url=f"https://example.com/{case}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = storage.add_content(item)

        if case == "favorited":
            storage.update_content_status(content_id, favorited=True)
            favorited_id = content_id
        elif case == "flagged":
            storage.flag_interesting(content_id)
            flagged_id = content_id
        elif case == "both":
            storage.update_content_status(content_id, favorited=True)
            storage.flag_interesting(content_id)
            both_id = content_id
        else:  # neither
            neither_id = content_id

    # Verify count excludes protected items
    count = storage.count_unprioritized()
    assert count == 1, "Only 'neither' item should be counted for prune"

    # Execute prune
    deleted_count = storage.delete_unprioritized()
    assert deleted_count == 1, "Should delete only the unprotected item"

    # Verify protected items still exist
    cursor = storage.conn.execute("SELECT id FROM content WHERE priority IS NULL")
    remaining_ids = {row[0] for row in cursor.fetchall()}

    assert favorited_id in remaining_ids, "Favorited item should be protected"
    assert flagged_id in remaining_ids, "Flagged item should be protected"
    assert both_id in remaining_ids, "Item with both flags should be protected"
    assert neither_id not in remaining_ids, "Unprotected item should be deleted"
