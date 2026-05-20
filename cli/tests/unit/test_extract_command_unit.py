"""Unit tests for extract.py CLI command logic.

Protects:
- INV: priority validation rejects invalid values before any API call
- INV: limit < 1 guard short-circuits without calling get_content()
- INV: client-side filter excludes items already with deep_extraction
- INV: per-item RuntimeError caught without stopping the batch
- INV: extract command registered in __main__.py (command discoverable)

Tests wrap the extract() function in a local typer app for invocation,
bypassing the full __main__.py app (avoids config-file and service dependencies).
APIClient is patched at the cli.extract module boundary to control responses.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import typer

# Add CLI src to path (matches pattern in test_api_client_search_params.py)
cli_src = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(cli_src))

from typer.testing import CliRunner  # noqa: E402

from cli.extract import extract  # noqa: E402

# Wrap the plain function in a local Typer app for test invocation.
# CliRunner.invoke() requires a Typer app, not a raw function.
_app = typer.Typer()
_app.command()(extract)

runner = CliRunner()


def _make_item(
    item_id: str = "abc123",
    title: str = "Test Article",
    has_extraction: bool = False,
) -> dict:
    """Build a minimal candidate item dict matching the API response shape."""
    analysis: dict = {}
    if has_extraction:
        analysis["deep_extraction"] = {
            "synthesis": "test synthesis",
            "model": "gpt-5-mini",
        }
    return {"id": item_id, "title": title, "analysis": analysis}


def test_invalid_priority_exits_before_api_call() -> None:
    """
    INVARIANT: Invalid --priority value exits code 1 before any API call.
    BREAKS: Bad requests reach the daemon, causing confusing server errors.

    The validation guard must fire before APIClient() is even constructed.
    """
    get_content_called = [0]

    def fake_get_content(*args, **kwargs):
        get_content_called[0] += 1
        return []

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.side_effect = fake_get_content
        result = runner.invoke(_app, ["--priority", "invalid"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 for invalid priority, got {result.exit_code}"
    )
    assert get_content_called[0] == 0, (
        "get_content() must NOT be called when priority is invalid"
    )


def test_limit_zero_short_circuits_without_api_call() -> None:
    """
    INVARIANT: --limit 0 produces 'No items need extraction' without calling get_content().
    BREAKS: limit * 3 = 0 hits the server's limit >= 1 floor validator, raising RuntimeError.

    The server's /api/entries enforces limit >= 1 via Pydantic; without the client-side
    guard, --limit 0 would surface as a red error message rather than the documented no-op.
    """
    get_content_called = [0]

    def fake_get_content(*args, **kwargs):
        get_content_called[0] += 1
        return []

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.side_effect = fake_get_content
        result = runner.invoke(_app, ["--limit", "0"])

    assert result.exit_code == 0, (
        f"Expected exit code 0 for --limit 0, got {result.exit_code}"
    )
    assert get_content_called[0] == 0, "get_content() must NOT be called when limit < 1"
    assert "No items need extraction" in result.output, (
        f"Expected 'No items need extraction' in output, got: {result.output!r}"
    )


def test_client_filter_excludes_already_extracted_items() -> None:
    """
    INVARIANT: Items with analysis.deep_extraction are excluded from the pending batch.
    BREAKS: Every run re-extracts all items (ignoring idempotency), wasting LLM credits.

    get_content() returns 3 items: 2 already extracted, 1 pending.
    extract_entry() must be called exactly once (for the pending item).
    """
    extracted_a = _make_item("id-a", "Already Extracted A", has_extraction=True)
    extracted_b = _make_item("id-b", "Already Extracted B", has_extraction=True)
    pending_c = _make_item("id-c", "Pending Article C", has_extraction=False)

    extract_calls = []

    def fake_extract_entry(entry_id: str) -> dict:
        extract_calls.append(entry_id)
        return {"deep_extraction": {"synthesis": "done"}}

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.return_value = [
            extracted_a,
            extracted_b,
            pending_c,
        ]
        MockClient.return_value.extract_entry.side_effect = fake_extract_entry
        result = runner.invoke(_app, ["--limit", "10"])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    assert extract_calls == ["id-c"], (
        f"extract_entry() must be called only for pending item 'id-c', got: {extract_calls}"
    )
    assert "Done: 1 extracted, 0 failed" in result.output, (
        f"Expected '1 extracted, 0 failed' in output, got: {result.output!r}"
    )


def test_per_item_failure_does_not_abort_batch() -> None:
    """
    INVARIANT: A RuntimeError from extract_entry() on one item does not stop subsequent items.
    BREAKS: One failing item (503, timeout, etc.) aborts the rest of the batch silently.

    Three pending items: first and third succeed, second fails.
    All three items must be attempted; summary must reflect partial success.
    """
    items = [
        _make_item("id-1", "Article One"),
        _make_item("id-2", "Article Two"),
        _make_item("id-3", "Article Three"),
    ]

    call_count = [0]

    def fake_extract_entry(entry_id: str) -> dict:
        call_count[0] += 1
        if entry_id == "id-2":
            raise RuntimeError("503 Service Unavailable")
        return {"deep_extraction": {"synthesis": "ok"}}

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.return_value = items
        MockClient.return_value.extract_entry.side_effect = fake_extract_entry
        result = runner.invoke(_app, ["--limit", "10"])

    assert result.exit_code == 0, (
        f"Expected exit code 0 (partial batch), got {result.exit_code}"
    )
    assert call_count[0] == 3, (
        f"All 3 items must be attempted; extract_entry() called {call_count[0]} times"
    )
    assert "Done: 2 extracted, 1 failed" in result.output, (
        f"Expected '2 extracted, 1 failed', got: {result.output!r}"
    )


def test_get_content_failure_exits_one_not_silent_noop() -> None:
    """
    INVARIANT: get_content() RuntimeError → exit 1 with red error message; NOT silent "no items".
    BREAKS: If the candidate-fetch error were swallowed or misrouted, the command would
    exit 0 printing "No items need extraction" — a data lie. The user thinks backfill
    is complete when the daemon was actually unreachable.

    Risk category: state transitions / data persistence — user's progress state is wrong.
    This is in the task's Test Considerations ("Network error on first call: RuntimeError
    from get_content() → exit 1; no items processed") but was absent from the original suite.
    """
    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.side_effect = RuntimeError(
            "Network error: Connection refused"
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


def test_limit_at_ceiling_passes_through_to_api_call() -> None:
    """
    INVARIANT: --limit 3333 (exact ceiling) does NOT fire the upper-bound guard.
    BREAKS: An off-by-one in the guard condition (>= 3333 instead of > 3333) would
    reject the highest valid input, making the documented ceiling unreachable.

    Mirrors test_invalid_priority_exits_before_api_call pattern.
    Taxonomy: data correctness — documented ceiling must be inclusive.
    """
    get_content_called = [0]

    def fake_get_content(*args, **kwargs):
        get_content_called[0] += 1
        return []

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.side_effect = fake_get_content
        result = runner.invoke(_app, ["--limit", "3333"])

    assert get_content_called[0] == 1, (
        f"get_content() must be called for --limit 3333 (valid ceiling); "
        f"called {get_content_called[0]} times — guard fired incorrectly"
    )
    assert result.exit_code == 0, (
        f"Expected exit code 0 for --limit 3333, got {result.exit_code}"
    )


def test_limit_above_ceiling_exits_before_api_call() -> None:
    """
    INVARIANT: --limit 3334 fires the upper-bound guard: exits 1, message names 3333,
    and get_content() is NOT called.
    BREAKS: limit * 3 = 10002 exceeds the server's le=10000 Pydantic validator on
    /api/entries, producing RuntimeError("Failed to list entries: Validation error")
    instead of a clear ceiling message.

    Mirrors test_invalid_priority_exits_before_api_call pattern.
    Taxonomy: data correctness — user gets a cryptic server error instead of guidance.
    """
    get_content_called = [0]

    def fake_get_content(*args, **kwargs):
        get_content_called[0] += 1
        return []

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.side_effect = fake_get_content
        result = runner.invoke(_app, ["--limit", "3334"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 for --limit 3334, got {result.exit_code}"
    )
    assert get_content_called[0] == 0, (
        "get_content() must NOT be called when limit > 3333"
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
    Mirrors test_invalid_priority_exits_before_api_call pattern.
    Taxonomy: data correctness — same guard, different input magnitude.
    """
    get_content_called = [0]

    def fake_get_content(*args, **kwargs):
        get_content_called[0] += 1
        return []

    with patch("cli.extract.APIClient") as MockClient:
        MockClient.return_value.get_content.side_effect = fake_get_content
        result = runner.invoke(_app, ["--limit", "10000"])

    assert result.exit_code == 1, (
        f"Expected exit code 1 for --limit 10000, got {result.exit_code}"
    )
    assert get_content_called[0] == 0, (
        "get_content() must NOT be called when limit > 3333"
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
    from cli.__main__ import app  # noqa: E402

    command_names = [cmd.name for cmd in app.registered_commands]
    assert "extract" in command_names, (
        f"'extract' command not found in registered commands: {command_names}"
    )
