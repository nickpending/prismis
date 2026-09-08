"""Unit tests for the notify link's observability record.

Invariants protected:
  - A notification command that exits 0 emits a success event with a count and duration
  - A notification command that exits non-zero emits an error event rather than only a
    log line — the refusal is acknowledged after the thing that can refuse has run
  - A command that cannot be executed at all emits an error event carrying the reason
  - Nothing to notify about emits a skipped event, distinguishable from both of the above

Success criteria covered:
  SC-5 (the dark links emit records)

Real collaborators throughout: real subprocess execution against real shell utilities,
no mock of Notifier or subprocess.
"""

import uuid

import pytest

from prismis_daemon.notifier import Notifier
from prismis_daemon.observability import get_logger, reset_logger, set_run_id
from prismis_daemon.verify_chain import read_run_events

_HIGH_ITEM = {"priority": "high", "title": "A high priority item"}


@pytest.fixture(autouse=True)
def _fresh_observability():
    """Bind the observability logger to this test's sealed data dir, and unbind after.

    The logger is a module-level singleton that caches the base directory it resolved
    when first constructed, and every test gets a different XDG_DATA_HOME — without the
    reset a test reads a directory an earlier test established (and, where that earlier
    test removed it, one that no longer exists). The run id is a module-level global for
    the same reason it is reset here: pytest runs the suite in one process.
    """
    reset_logger()
    yield
    set_run_id(None)
    reset_logger()


def _notification_events(run_id: str) -> list[dict]:
    events = read_run_events(get_logger().base_dir, run_id)
    return [e for e in events if e["event"] == "notification.send"]


def test_successful_command_emits_success_event() -> None:
    """
    SC-5: a notification that the command accepted leaves a success record.
    BREAKS: the notify link is dark and the chain cannot tell a sent notification from
    one that was never attempted.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    Notifier({"command": "echo"}).notify_new_content([_HIGH_ITEM])

    events = _notification_events(run_id)
    assert len(events) == 1
    assert events[0]["status"] == "success"
    assert events[0]["count"] == 1
    assert isinstance(events[0]["duration_ms"], int)


def test_non_zero_exit_emits_error_event() -> None:
    """
    SC-5 / Principle II: a command that runs and refuses is an error event, not a
    success and not silence. `false` exits 1 whatever arguments it is given.

    BREAKS: the exact case this instrumentation exists for — the notifier acknowledging
    an action before the thing that can refuse it has answered.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    Notifier({"command": "false"}).notify_new_content([_HIGH_ITEM])

    events = _notification_events(run_id)
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert events[0]["count"] == 1
    assert isinstance(events[0]["duration_ms"], int)


def test_unrunnable_command_emits_error_event_carrying_the_reason() -> None:
    """
    SC-5: the exception path (the command binary does not exist) is recorded too, and
    is distinguishable from a non-zero exit by carrying the failure text.

    BREAKS: the notifier's own except clause swallows the failure with only a warning,
    which is what it did before this work.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    Notifier({"command": "prismis-no-such-notifier-binary"}).notify_new_content(
        [_HIGH_ITEM]
    )

    events = _notification_events(run_id)
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert "prismis-no-such-notifier-binary" in events[0]["error"]


def test_nothing_high_priority_emits_skipped_event() -> None:
    """
    Principle II: "there was nothing to send" is its own answer, not a blank.
    BREAKS: an empty notify is indistinguishable from a failed or never-run one.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    Notifier({"command": "echo"}).notify_new_content(
        [{"priority": "low", "title": "not high"}]
    )

    events = _notification_events(run_id)
    assert len(events) == 1
    assert events[0]["status"] == "skipped"
    assert events[0]["reason"] == "no_high_priority_items"
    assert events[0]["count"] == 0
