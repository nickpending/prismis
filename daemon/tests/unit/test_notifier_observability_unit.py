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

from prismis_daemon.notifier import Notifier
from prismis_daemon.observability import get_logger, set_run_id
from prismis_daemon.verify_chain import read_run_events

_HIGH_ITEM = {"priority": "high", "title": "A high priority item"}


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


def test_missing_command_is_one_skipped_record_and_one_warning_not_a_traceback(
    caplog,
) -> None:
    """
    On a host without the notifier command (cerebro, a Linux daemon, has no
    terminal-notifier) every cycle with a HIGH item logged a FileNotFoundError
    traceback: 94 in one week, all saying the same thing. A command that is not
    installed is a known condition, not a failure to diagnose: the record says
    `skipped` with the reason, the log warns once naming the command, and later
    cycles stay quiet.
    BREAKS: a log that buries real failures under one repeated traceback.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    notifier = Notifier({"command": "prismis-notifier-that-does-not-exist-7f3a"})

    with caplog.at_level("WARNING", logger="prismis_daemon.notifier"):
        notifier.notify_new_content([_HIGH_ITEM])
        notifier.notify_new_content([_HIGH_ITEM])

    events = _notification_events(run_id)
    assert [e["status"] for e in events] == ["skipped", "skipped"]
    assert all(e["reason"] == "command_not_found" for e in events)
    assert all(e["command"] == "prismis-notifier-that-does-not-exist-7f3a" for e in events)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "prismis-notifier-that-does-not-exist-7f3a" in warnings[0].getMessage()
    assert warnings[0].exc_info is None, "a missing command is not a traceback"
