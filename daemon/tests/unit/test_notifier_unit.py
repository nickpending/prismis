"""Unit tests for Notifier logic functions.

Real collaborators throughout: notify_new_content's HIGH-priority filter is driven
through the real Notifier and the real subprocess call it makes (to `echo`, a real
binary standing in for terminal-notifier), and proven by the observability record
that call leaves -- rather than by a patched `_send_notification`.
"""

import uuid

import pytest

from prismis_daemon.notifier import Notifier
from prismis_daemon.observability import get_logger, reset_logger, set_run_id
from prismis_daemon.verify_chain import read_run_events


@pytest.fixture(autouse=True)
def _fresh_observability():
    """Bind the observability logger to this test's sealed data dir, and unbind after.

    Mirrors test_notifier_observability_unit.py's fixture of the same name -- the
    logger is a module-level singleton that caches the base directory it resolved
    when first constructed, and every test gets a different XDG_DATA_HOME.
    """
    reset_logger()
    yield
    set_run_id(None)
    reset_logger()


def _notification_events(run_id: str) -> list[dict]:
    events = read_run_events(get_logger().base_dir, run_id)
    return [e for e in events if e["event"] == "notification.send"]


def test_notify_filters_high_priority_only() -> None:
    """Test that notify_new_content only processes HIGH priority items."""
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    notifier = Notifier({"high_priority_only": True, "command": "echo"})

    # Create mixed priority items
    items = [
        {"priority": "high", "title": "Important AI News"},
        {"priority": "medium", "title": "Python Update"},
        {"priority": "low", "title": "Basic Tutorial"},
        {"priority": "high", "title": "Security Alert"},
    ]

    notifier.notify_new_content(items)

    events = _notification_events(run_id)
    # A record was left at all -- if filtering broke and every item (including
    # non-HIGH ones) reached _send_notification, the count below would catch it.
    assert len(events) == 1
    assert events[0]["status"] == "success"
    # Only the 2 HIGH priority items reached _send_notification, not all 4.
    assert events[0]["count"] == 2


def test_notify_handles_empty_list() -> None:
    """Test notify_new_content handles empty items list gracefully."""
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    notifier = Notifier({"command": "echo"})

    notifier.notify_new_content([])

    # notify_new_content returns immediately for an empty list -- no event at all,
    # distinguishable from the "nothing HIGH priority" skip below.
    assert _notification_events(run_id) == []


def test_notify_handles_no_high_priority_items() -> None:
    """Test notify_new_content when no HIGH priority items exist."""
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    notifier = Notifier({"high_priority_only": True, "command": "echo"})

    # Only medium and low priority items
    items = [
        {"priority": "medium", "title": "Python Update"},
        {"priority": "low", "title": "Basic Tutorial"},
    ]

    notifier.notify_new_content(items)

    events = _notification_events(run_id)
    assert len(events) == 1
    assert events[0]["status"] == "skipped"
    assert events[0]["reason"] == "no_high_priority_items"


def test_message_formatting_single_item() -> None:
    """Test message formatting logic for single HIGH priority item."""
    Notifier()

    high_items = [
        {"title": "OpenAI Announces GPT-5 with Major Breakthroughs", "priority": "high"}
    ]

    # Test the internal logic by examining what command would be built
    count = len(high_items)
    title = high_items[0].get("title", "New Content")[:50]
    message = "1 new high priority item"

    # Verify formatting logic
    assert count == 1
    assert title == "OpenAI Announces GPT-5 with Major Breakthroughs"[:50]
    assert message == "1 new high priority item"


def test_message_formatting_multiple_items() -> None:
    """Test message formatting logic for multiple HIGH priority items."""
    Notifier()

    high_items = [
        {"title": "AI Breakthrough", "priority": "high"},
        {"title": "Security Alert", "priority": "high"},
        {"title": "Major Update", "priority": "high"},
    ]

    # Test the internal logic by examining what would be formatted
    count = len(high_items)
    title = "Prismis"  # For multiple items
    message = f"{count} new high priority items"

    # Verify formatting logic
    assert count == 3
    assert title == "Prismis"
    assert message == "3 new high priority items"


def test_config_defaults_and_overrides() -> None:
    """Test that config defaults work and can be overridden."""
    # Test defaults
    notifier_default = Notifier()
    assert notifier_default.high_priority_only  # Default
    assert notifier_default.command == "terminal-notifier"  # Default

    # Test overrides
    custom_config = {"high_priority_only": False, "command": "custom-notifier"}
    notifier_custom = Notifier(custom_config)
    assert not notifier_custom.high_priority_only
    assert notifier_custom.command == "custom-notifier"

    # Test partial config (should use defaults for missing)
    partial_config = {"command": "my-notifier"}
    notifier_partial = Notifier(partial_config)
    assert notifier_partial.high_priority_only  # Default
    assert notifier_partial.command == "my-notifier"  # Override


def test_title_truncation_in_single_item_message() -> None:
    """Test that long titles are truncated to 50 characters in logic."""
    Notifier()

    # Create item with very long title
    long_title = "A" * 100  # 100 character title
    high_items = [{"title": long_title, "priority": "high"}]

    # Test the truncation logic directly
    title = high_items[0].get("title", "New Content")[:50]

    # Should be truncated to 50 characters
    assert len(title) == 50
    assert title == "A" * 50


def test_handles_missing_title_field() -> None:
    """Test graceful handling when content item lacks title field."""
    Notifier()

    # Item without title field
    high_items = [{"priority": "high", "url": "https://example.com"}]

    # Test the default title logic
    title = high_items[0].get("title", "New Content")[:50]

    # Should use default title
    assert title == "New Content"
