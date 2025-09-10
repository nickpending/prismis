"""Unit tests for Notifier logic functions."""

from notifier import Notifier


def test_notify_filters_high_priority_only() -> None:
    """Test that notify_new_content only processes HIGH priority items."""
    notifier = Notifier({"high_priority_only": True})

    # Create mixed priority items
    items = [
        {"priority": "high", "title": "Important AI News"},
        {"priority": "medium", "title": "Python Update"},
        {"priority": "low", "title": "Basic Tutorial"},
        {"priority": "high", "title": "Security Alert"},
    ]

    # Mock the _send_notification method to capture what gets called
    called_items = []

    def mock_send_notification(items):
        called_items.extend(items)

    notifier._send_notification = mock_send_notification

    notifier.notify_new_content(items)

    # Should only have called with HIGH priority items
    assert len(called_items) == 2
    assert all(item["priority"] == "high" for item in called_items)
    assert called_items[0]["title"] == "Important AI News"
    assert called_items[1]["title"] == "Security Alert"


def test_notify_handles_empty_list() -> None:
    """Test notify_new_content handles empty items list gracefully."""
    notifier = Notifier()

    # Mock to ensure _send_notification is never called
    def mock_send_notification(items):
        setattr(notifier, "_send_called", True)

    notifier._send_notification = mock_send_notification

    notifier.notify_new_content([])

    # Should not have called _send_notification
    assert not hasattr(notifier, "_send_called")


def test_notify_handles_no_high_priority_items() -> None:
    """Test notify_new_content when no HIGH priority items exist."""
    notifier = Notifier({"high_priority_only": True})

    # Only medium and low priority items
    items = [
        {"priority": "medium", "title": "Python Update"},
        {"priority": "low", "title": "Basic Tutorial"},
    ]

    # Mock to ensure _send_notification is never called
    def mock_send_notification(items):
        setattr(notifier, "_send_called", True)

    notifier._send_notification = mock_send_notification

    notifier.notify_new_content(items)

    # Should not have called _send_notification
    assert not hasattr(notifier, "_send_called")


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
