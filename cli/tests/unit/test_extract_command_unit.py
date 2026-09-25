"""Unit tests for extract.py CLI command logic.

Protects:
- INV: priority validation rejects invalid values before any API call
- INV: limit < 1 guard short-circuits without calling get_content()
- INV: client-side filter excludes items already with deep_extraction
- INV: per-item RuntimeError caught without stopping the batch
- INV: extract command registered in __main__.py (command discoverable)

Tests invoke the real `extract()` Typer command, wrapped in a local app for
CliRunner (bypassing the full __main__.py app, which needs a fuller config than
this command does). `extract()` constructs its own `cli.extract.APIClient()`
in-body — there is no injection point — so the guard tests below prove their
"no API call happened" invariant the way `no_network` does in
daemon/tests/conftest.py: by leaving no config.toml in place, so a real call
would surface as a visibly different crash/message rather than passing silently.
The tests that need a real API call route through `live_daemon` / `live_daemon_deep`
(conftest.py), which also point the sealed config's [remote] section at the
fixture's real daemon — `APIClient()` reaches it with no code change needed here.
"""

import socket
from datetime import UTC, datetime, timedelta

import typer
from typer.testing import CliRunner

from cli.extract import extract

from conftest import (
    DEEP_EXTRACT_FAILURE_MARKER,
    LiveDaemon,
    seed_content,
)
from prismis_daemon.storage import Storage

# Wrap the plain function in a local Typer app for test invocation.
# CliRunner.invoke() requires a Typer app, not a raw function.
_app = typer.Typer()
_app.command()(extract)

runner = CliRunner()


def test_invalid_priority_exits_before_api_call() -> None:
    """
    INVARIANT: Invalid --priority value exits code 1 before any API call.
    BREAKS: Bad requests reach the daemon, causing confusing server errors.

    No config.toml exists in the sealed XDG dirs (isolated_xdg_env only creates
    the directory), so if the validation guard failed to fire, `APIClient()`
    would raise "Config file not found" instead of printing this message —
    a real, distinguishable crash rather than a silently-passing test.
    """
    result = runner.invoke(_app, ["--priority", "invalid"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 for invalid priority, got {result.exit_code}"
    )
    assert "Invalid --priority" in result.output, (
        f"get_content() must NOT be reached when priority is invalid — expected "
        f"the validation message, got: {result.output!r}"
    )


def test_limit_zero_short_circuits_without_api_call() -> None:
    """
    INVARIANT: --limit 0 produces 'No items need extraction' without calling get_content().
    BREAKS: limit * 3 = 0 hits the server's limit >= 1 floor validator, raising RuntimeError.

    The server's /api/entries enforces limit >= 1 via Pydantic; without the client-side
    guard, --limit 0 would surface as a red error message rather than the documented no-op.
    No config.toml exists, so a bypassed guard would crash instead of matching this output.
    """
    result = runner.invoke(_app, ["--limit", "0"])

    assert result.exit_code == 0, (
        f"Expected exit code 0 for --limit 0, got {result.exit_code}"
    )
    assert "No items need extraction" in result.output, (
        f"Expected 'No items need extraction' in output, got: {result.output!r}"
    )


def test_client_filter_excludes_already_extracted_items(
    live_daemon_deep: LiveDaemon,
) -> None:
    """
    INVARIANT: Items with analysis.deep_extraction are excluded from the pending batch.
    BREAKS: Every run re-extracts all items (ignoring idempotency), wasting LLM credits.

    Seeds 3 real HIGH-priority items: 2 already carry `analysis.deep_extraction`,
    1 does not. The real daemon returns all 3; the CLI's own client-side filter
    must be what excludes the two already-extracted ones, since the one real
    extraction attempt this drives (the pending item) must be the only one that
    happens — a second, unwanted attempt on an already-extracted item would show
    up as "2 extracted" instead of "1 extracted, 0 failed".
    """
    storage = Storage(live_daemon_deep.db_path)
    source_id = storage.add_source("http://example.com/feed", "rss", "Example")
    seed_content(
        storage,
        source_id,
        title="Already Extracted A",
        analysis={"deep_extraction": {"synthesis": "old", "model": "stub"}},
    )
    seed_content(
        storage,
        source_id,
        title="Already Extracted B",
        analysis={"deep_extraction": {"synthesis": "old", "model": "stub"}},
    )
    seed_content(storage, source_id, title="Pending Article C")
    storage.close()

    result = runner.invoke(_app, ["--limit", "10"])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    assert "Done: 1 extracted, 0 failed" in result.output, (
        f"Expected '1 extracted, 0 failed' in output, got: {result.output!r}"
    )


def test_per_item_failure_does_not_abort_batch(live_daemon_deep: LiveDaemon) -> None:
    """
    INVARIANT: A RuntimeError from extract_entry() on one item does not stop subsequent items.
    BREAKS: One failing item (503, timeout, etc.) aborts the rest of the batch silently.

    Three real pending items, ordered by `published_at` (newest first, matching
    `GET /api/entries`' own sort) so the middle one is attempted second: its
    content carries `DEEP_EXTRACT_FAILURE_MARKER`, which the stub LLM
    (live_daemon_deep, conftest.py) answers with a body that fails
    ContentDeepExtractor's own JSON parse — a real 500 from the real daemon, not
    an injected exception. All three items must still be attempted.
    """
    storage = Storage(live_daemon_deep.db_path)
    source_id = storage.add_source("http://example.com/feed", "rss", "Example")
    now = datetime.now(UTC)
    seed_content(
        storage, source_id, title="Article One", published_at=now
    )
    seed_content(
        storage,
        source_id,
        title="Article Two",
        content=f"Body containing {DEEP_EXTRACT_FAILURE_MARKER} to force a real failure.",
        published_at=now - timedelta(minutes=1),
    )
    seed_content(
        storage, source_id, title="Article Three", published_at=now - timedelta(minutes=2)
    )
    storage.close()

    result = runner.invoke(_app, ["--limit", "10"])

    assert result.exit_code == 0, (
        f"Expected exit code 0 (partial batch), got {result.exit_code}"
    )
    assert "Done: 2 extracted, 1 failed" in result.output, (
        f"Expected '2 extracted, 1 failed', got: {result.output!r}"
    )


def test_get_content_failure_exits_one_not_silent_noop(
    isolated_xdg_env,
) -> None:
    """
    INVARIANT: get_content() RuntimeError → exit 1 with red error message; NOT silent "no items".
    BREAKS: If the candidate-fetch error were swallowed or misrouted, the command would
    exit 0 printing "No items need extraction" — a data lie. The user thinks backfill
    is complete when the daemon was actually unreachable.

    Points the sealed config's [remote] section at a real closed loopback port
    (bound, then closed, so nothing answers): a real httpx.ConnectError reaches
    get_content(), the same real-network-boundary technique as
    test_extract_entry_wraps_network_error_as_runtime_error.

    Risk category: state transitions / data persistence — user's progress state is wrong.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    host, port = probe.getsockname()
    probe.close()

    isolated_xdg_env.joinpath("config.toml").write_text(
        f'[remote]\nurl = "http://{host}:{port}"\nkey = "unused"\n'
    )

    result = runner.invoke(_app, ["--priority", "high", "--limit", "5"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 when get_content() fails, got {result.exit_code}; "
        f"exit 0 here means the user falsely believes backfill succeeded"
    )
    assert "Failed to list entries" in result.output, (
        f"Expected 'Failed to list entries' error message in output, got: {result.output!r}"
    )
    # Crucially: must NOT print the misleading success message
    assert "No items need extraction" not in result.output, (
        "Must not print 'No items need extraction' when the candidate fetch failed"
    )


def test_limit_at_ceiling_passes_through_to_api_call(live_daemon: LiveDaemon) -> None:
    """
    INVARIANT: --limit 3333 (exact ceiling) does NOT fire the upper-bound guard.
    BREAKS: An off-by-one in the guard condition (>= 3333 instead of > 3333) would
    reject the highest valid input, making the documented ceiling unreachable.

    `live_daemon` (no deep extraction configured) has an empty, real, sealed DB:
    a real get_content() call against it always answers with 0 items. If the
    guard incorrectly fired at exactly 3333, the output would carry the distinct
    "exceeds maximum" ceiling message and exit 1 instead — this and that are the
    only two ways this command can end, so asserting on the real success message
    is itself proof the real call happened.
    """
    result = runner.invoke(_app, ["--limit", "3333"])

    assert result.exit_code == 0, (
        f"Expected exit code 0 for --limit 3333, got {result.exit_code}"
    )
    assert "No items need extraction" in result.output, (
        f"get_content() must be called for --limit 3333 (valid ceiling) and the "
        f"real (empty) daemon must answer normally; got: {result.output!r}"
    )


def test_limit_above_ceiling_exits_before_api_call() -> None:
    """
    INVARIANT: --limit 3334 fires the upper-bound guard: exits 1, message names 3333,
    and get_content() is NOT called.
    BREAKS: limit * 3 = 10002 exceeds the server's le=10000 Pydantic validator on
    /api/entries, producing RuntimeError("Failed to list entries: Validation error")
    instead of a clear ceiling message.

    No config.toml exists, so a bypassed guard would crash with a config error
    instead of producing this message.
    """
    result = runner.invoke(_app, ["--limit", "3334"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 for --limit 3334, got {result.exit_code}"
    )
    assert "3333" in result.output, (
        f"Error message must name the 3333 ceiling so user knows the valid max; "
        f"got: {result.output!r}"
    )


def test_limit_well_above_ceiling_also_fires_guard() -> None:
    """
    INVARIANT: --limit 10000 fires the same upper-bound guard (not a different code path).
    BREAKS: A hypothetical condition that only gates 3334-9999 but lets 10000 through
    would allow limit * 3 = 30000 to reach the server, producing a 422 error.

    Confirms the guard is `> 3333`, not an exact-value check.
    """
    result = runner.invoke(_app, ["--limit", "10000"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 for --limit 10000, got {result.exit_code}"
    )
    assert "3333" in result.output, (
        f"Error message must name the 3333 ceiling; got: {result.output!r}"
    )


def test_help_output_mentions_ceiling() -> None:
    """
    INVARIANT: --help text for --limit names the 3333 ceiling.
    BREAKS: Users discover the ceiling only by hitting it with a red error — no
    upfront documentation in the command's own help text.

    The typer Option at extract.py:22 sets help="Maximum items to process (default: 10, max: 3333)".
    If someone changes the ceiling constant without updating the help text, the mismatch
    goes unnoticed until a user reads a help string that lies.
    """
    result = runner.invoke(_app, ["--help"])

    assert result.exit_code == 0, f"--help must exit 0, got {result.exit_code}"
    assert "3333" in result.output, (
        f"--help output must mention the 3333 ceiling so users know the limit before hitting it; "
        f"got: {result.output!r}"
    )


def test_extract_command_registered_in_main() -> None:
    """
    INVARIANT: 'extract' command is registered in __main__.py's app.
    BREAKS: Users get 'No such command' error; the command doesn't exist.

    Verifies the registration line `app.command(name="extract", ...)(extract.extract)`
    is present and the command name is discoverable from the app.
    """
    from cli.__main__ import app

    command_names = [cmd.name for cmd in app.registered_commands]
    assert "extract" in command_names, (
        f"'extract' command not found in registered commands: {command_names}"
    )
