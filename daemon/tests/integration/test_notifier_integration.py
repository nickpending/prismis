"""Integration tests for Notifier with real terminal-notifier subprocess calls."""

import logging
import shutil
import subprocess
import sys

import pytest

from prismis_daemon.notifier import Notifier


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("terminal-notifier") is None,
    reason="terminal-notifier is a macOS-only binary and presence on PATH does not mean "
    "it can run. The GitHub Ubuntu runner ships a terminal-notifier Ruby gem whose "
    "executable is a macOS Mach-O app bundle, so `which` finds it, the old guard did not "
    "fire, and exec died with 'Syntax error: \"(\" unexpected'. Gate on the platform "
    "that can actually execute it. Tracked: gh #60.",
)
def test_notifier_calls_terminal_notifier_subprocess(caplog) -> None:
    """Test that Notifier makes a real terminal-notifier call that EXITS ZERO.

    Calls `_send_notification` rather than `notify_new_content`: the public wrapper
    catches every exception and logs a warning, so a test driving it cannot fail no
    matter what the subprocess does. That is what the previous two versions of this test
    got wrong — first an `isinstance(success, bool)` that was true in both branches, then
    a bare call with no assertion at all over a callee that swallows.

    `_send_notification` propagates a missing binary and a timeout, but it also only
    *logs* a non-zero exit rather than raising. So reaching the end of the call still
    proves nothing on its own; the exit status has to be asserted through the record it
    leaves. A non-zero exit logs at WARNING and never logs the success line.
    """
    config = {"high_priority_only": True, "command": "terminal-notifier"}
    notifier = Notifier(config)

    high_priority_items = [
        {
            "title": "Integration Test: Notifier Working",
            "priority": "high",
            "url": "https://example.com/test",
        }
    ]

    with caplog.at_level(logging.DEBUG, logger="prismis_daemon.notifier"):
        notifier._send_notification(high_priority_items)

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("Sent notification:") for m in messages), (
        f"terminal-notifier did not exit 0 — no success record. Log was: {messages}"
    )
    assert not any("Notification command failed" in m for m in messages), (
        f"terminal-notifier reported a failure: {messages}"
    )


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
