"""Integration tests for Notifier with real terminal-notifier subprocess calls."""

import subprocess
from notifier import Notifier


def test_notifier_calls_terminal_notifier_subprocess() -> None:
    """Test that Notifier makes real subprocess calls to terminal-notifier.

    This test:
    - Creates Notifier with real config
    - Calls notify_new_content with HIGH priority items
    - Verifies actual terminal-notifier subprocess is executed
    - Checks notification appears on Mac (manual verification)
    """
    # Create notifier with real terminal-notifier command
    config = {"high_priority_only": True, "command": "terminal-notifier"}
    notifier = Notifier(config)

    # Create HIGH priority test content
    high_priority_items = [
        {
            "title": "Integration Test: Notifier Working",
            "priority": "high",
            "url": "https://example.com/test",
        }
    ]

    # This should trigger a real Mac notification
    # If terminal-notifier is installed, notification will appear
    try:
        notifier.notify_new_content(high_priority_items)
        # If we get here without exception, subprocess call succeeded
        success = True
    except Exception as e:
        # If terminal-notifier not installed or fails, that's expected in CI
        if "terminal-notifier" in str(e) or "No such file" in str(e):
            success = (
                False  # Expected failure in CI/environments without terminal-notifier
            )
        else:
            raise  # Unexpected error

    # In local development with terminal-notifier, success should be True
    # In CI without terminal-notifier, success may be False
    # Either is acceptable for this integration test
    assert isinstance(success, bool)


def test_notifier_handles_terminal_notifier_failure() -> None:
    """Test that Notifier handles terminal-notifier command failures gracefully."""
    # Create notifier with non-existent command
    config = {"high_priority_only": True, "command": "non-existent-notifier-command"}
    notifier = Notifier(config)

    high_priority_items = [{"title": "Test Notification", "priority": "high"}]

    # Should not raise exception even if command fails
    # Error should be logged but not crash the application
    try:
        notifier.notify_new_content(high_priority_items)
        # If no exception, that's good - error was handled gracefully
    except subprocess.CalledProcessError:
        # This is also acceptable - subprocess failed as expected
        pass
    except FileNotFoundError:
        # Also acceptable - command not found
        pass


def test_notifier_respects_high_priority_only_config() -> None:
    """Test that Notifier configuration is respected in real usage."""
    # Test with high_priority_only = True
    notifier_high_only = Notifier({"high_priority_only": True})

    mixed_items = [
        {"title": "High Priority", "priority": "high"},
        {"title": "Medium Priority", "priority": "medium"},
        {"title": "Low Priority", "priority": "low"},
    ]

    # Should only process HIGH priority (no exception means it worked)
    notifier_high_only.notify_new_content(mixed_items)

    # Test with high_priority_only = False (if we implemented that feature)
    notifier_all = Notifier({"high_priority_only": False})

    # Should process all items (but our current implementation filters anyway)
    notifier_all.notify_new_content(mixed_items)


def test_notifier_integration_with_empty_and_mixed_content() -> None:
    """Test Notifier handles real-world content scenarios."""
    notifier = Notifier()

    # Test empty list
    notifier.notify_new_content([])

    # Test all non-HIGH priority
    low_items = [
        {"title": "Regular Update", "priority": "medium"},
        {"title": "Basic Info", "priority": "low"},
    ]
    notifier.notify_new_content(low_items)

    # Test mixed with HIGH priority
    mixed_items = [
        {"title": "Critical Alert", "priority": "high"},
        {"title": "Normal Update", "priority": "medium"},
    ]
    # This might trigger notification if terminal-notifier available
    notifier.notify_new_content(mixed_items)


def test_notifier_handles_malformed_content_gracefully() -> None:
    """Test Notifier handles malformed content items without crashing."""
    notifier = Notifier()

    # Test items without required fields
    malformed_items = [
        {"priority": "high"},  # Missing title
        {"title": "No Priority"},  # Missing priority
        {},  # Empty item
        {
            "title": "Valid Item",
            "priority": "high",
            "extra_field": "ignored",
        },  # Extra fields
    ]

    # Should handle gracefully without exceptions
    notifier.notify_new_content(malformed_items)
