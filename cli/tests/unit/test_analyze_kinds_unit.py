"""Unit tests for `prismis-cli analyze kinds` and `analyze repair` -- kind-backfill
work order job 1 (SC-1..SC-5, gh #83) and dedup work order job 10's cluster-13 fix
(SC-10).

Protects:
- SC-1: selection is bounded to items carrying a summary whose analysis has no
  kind_confidence key at all -- not merely a null value, since an unclassified item
  stores kind_confidence=null too and must never be reselected -- newest fetched_at
  first, at most --limit, and narrowed further by --since-days when given.
- SC-2: each selected item's stored analysis gains kind and kind_confidence, every
  other analysis field is left untouched, and the command never reimplements
  KindClassifier's own confidence threshold -- it is driven through classify()
  itself, so a value exactly at CONFIDENCE_THRESHOLD classifies and one epsilon below
  does not, the same as kind_classifier's own unit tests prove for classify().
- SC-3: a per-item classifier failure is isolated -- the run continues, the failed
  item's analysis is left without a kind_confidence key so a later run retries it,
  and the run ends by printing classified/unclassified/failed counts.
- SC-4: the command refuses -- before any decisions-endpoint call -- when no
  kind_service is configured (naming kind_service) or when run in remote mode
  (naming local mode the way `analyze repair`'s own `_check_local_mode` does).
- SC-10: `analyze repair` builds its analysis dict through the same daemon-side
  helper (build_llm_analysis) the fetch pipeline uses, and now passes learned
  preferences into the evaluator the same way orchestrator.run_once does --
  proven by preference_influenced appearing in the stored analysis, True when
  enough recent feedback exists and False (not absent) when it does not.

Per constitution Principle I (daemon/tests/unit/test_no_internal_mocks_unit.py),
submit_decision (kind_classifier's decisions-endpoint boundary) and
prismis_daemon.llm_call.complete (the shared LLM-call boundary summarizer and
evaluator now go through, cluster 11) are the only things stood in for. Storage,
Config, ContentSummarizer, ContentEvaluator, and KindClassifier all run for real
against a real temp database and a real sealed config.toml.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

import pytest
from typer.testing import CliRunner

from cli.analyze import app as analyze_app
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.database import init_db
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD
from prismis_daemon.kind_classifier import CONFIDENCE_THRESHOLD
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

# The decisions-endpoint provider boundary itself (Principle I permits faking it) --
# same patch target kind_classifier's own unit tests use.
_PATCH_SUBMIT = "prismis_daemon.kind_classifier.submit_decision"  # claudex-guard: allow-mock

# The shared LLM-call boundary summarizer.py and evaluator.py both go through
# since cluster 11's call_llm_with_circuit_breaker extraction -- same patch target
# daemon/tests/unit/test_evaluator_unit.py and test_summarizer_unit.py use.
_PATCH_COMPLETE = "prismis_daemon.llm_call.complete"  # claudex-guard: allow-mock

runner = CliRunner()


class _FakeDecisionCall:
    """Minimal stand-in for kind_classifier.DecisionCall."""

    def __init__(self, answers: dict, model: str = "typesafe/jev-1.13-test") -> None:
        self.answers = answers
        self.model = model
        self.cost = 0.00004
        self.duration_ms = 12


def _kind_answer(choice: object, confidence: object) -> dict:
    answer: dict = {"type": "choice"}
    if choice is not None:
        answer["choice"] = choice
    if confidence is not None:
        answer["confidence"] = confidence
    return {"kind": answer}


def _fixed_decision(choice: str, confidence: float):
    def _fake(state: dict, *, service: str, model: str | None = None):
        return _FakeDecisionCall(_kind_answer(choice, confidence))

    return _fake


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test (module-global registry)."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _write_config(cfg_dir: Path, *, kind_service: str | None, remote_url: str | None = None) -> None:
    text = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    if kind_service:
        text = text.replace(
            'light_service = "prismis-openai"\n',
            f'light_service = "prismis-openai"\nkind_service = "{kind_service}"\n',
        )
    if remote_url:
        text = text.replace(
            "[remote]\n", f'[remote]\nurl = "{remote_url}"\nkey = "unused"\n'
        )
    cfg_dir.joinpath("config.toml").write_text(text)
    cfg_dir.joinpath("context.md").write_text(DEFAULT_CONTEXT_MD)


@pytest.fixture
def local_env(isolated_xdg_env: Path) -> Path:
    """A sealed local install: kind_service configured, DB at Storage()'s own default
    resolution path ($XDG_DATA_HOME/prismis/prismis.db) so the command under test --
    which builds its own Storage() with no injection point -- reaches it."""
    _write_config(isolated_xdg_env, kind_service="prismis-openrouter-kind")

    data_home = Path(os.environ["XDG_DATA_HOME"])
    db_path = data_home / "prismis" / "prismis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    return db_path


@pytest.fixture
def local_env_no_kind_service(isolated_xdg_env: Path) -> Path:
    """A sealed local install with no kind_service configured at all (SC-4)."""
    _write_config(isolated_xdg_env, kind_service=None)

    data_home = Path(os.environ["XDG_DATA_HOME"])
    db_path = data_home / "prismis" / "prismis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    return db_path


def _seed(
    storage: Storage,
    source_id: str,
    *,
    title: str,
    summary: str | None = "A short summary of the item.",
    analysis: dict[str, Any] | None = None,
    fetched_at: datetime | None = None,
    content: str = "Article body long enough to classify against.",
) -> str:
    """Insert one content row with full control over summary/analysis/fetched_at --
    seed_content (conftest.py) exposes neither, so this test file seeds its own."""
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=str(uuid.uuid4()),
            title=title,
            url=f"http://example.com/{uuid.uuid4().hex[:8]}",
            content=content,
            summary=summary,
            analysis=analysis,
            fetched_at=fetched_at,
        )
    )
    assert content_id is not None, "expected a newly inserted content id, got a duplicate"
    return content_id


# ---------------------------------------------------------------------------
# SC-1: selection -- summary present, no kind_confidence key, newest first,
# bounded by --limit and --since-days
# ---------------------------------------------------------------------------


def test_kinds_selects_items_with_summary_and_no_kind_confidence_key(
    local_env: Path,
) -> None:
    """
    SC-1: only an item with a summary and no kind_confidence key in its analysis is
    selected -- an already-classified item, an already-unclassified item (whose
    kind_confidence is stored as null, not absent), and an item with no summary at
    all are all excluded.
    BREAKS: json_extract() instead of json_type() for the "no kind_confidence" check
    would treat a stored null the same as an absent key, reselecting unclassified
    items forever.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    _seed(storage, source_id, title="Needs Classification", analysis={"reading_summary": "x"})
    _seed(
        storage,
        source_id,
        title="Already Classified",
        analysis={"kind": "release", "kind_confidence": 0.9},
    )
    _seed(
        storage,
        source_id,
        title="Already Unclassified",
        analysis={"kind": None, "kind_confidence": None},
    )
    _seed(storage, source_id, title="No Summary At All", summary=None, analysis={})
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=_fixed_decision("tutorial", 0.95)):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])

    assert result.exit_code == 0, result.output
    assert "Found 1" in result.output, result.output
    assert "Needs Classification" in result.output
    assert "Already Classified" not in result.output
    assert "Already Unclassified" not in result.output
    assert "No Summary At All" not in result.output


def test_kinds_selects_newest_first_and_at_most_limit(local_env: Path) -> None:
    """
    SC-1: selection orders by fetched_at newest first and returns at most --limit.
    BREAKS: An unordered or oldest-first selection processes low-value backlog before
    the newest items an operator most wants classified first.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    now = datetime.now(UTC)
    _seed(storage, source_id, title="Oldest Item", analysis={}, fetched_at=now - timedelta(days=2))
    _seed(storage, source_id, title="Middle Item", analysis={}, fetched_at=now - timedelta(days=1))
    _seed(storage, source_id, title="Newest Item", analysis={}, fetched_at=now)
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=_fixed_decision("tutorial", 0.95)):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "Found 2" in result.output, result.output
    assert "Newest Item" in result.output
    assert "Middle Item" in result.output
    assert "Oldest Item" not in result.output
    assert result.output.index("Newest Item") < result.output.index("Middle Item"), (
        "expected the newest item processed before the middle one"
    )


def test_kinds_selects_only_items_fetched_within_since_days_when_given(
    local_env: Path,
) -> None:
    """
    SC-1: --since-days narrows selection to items fetched within the last D days.
    BREAKS: Selection ignores --since-days and reprocesses the whole backlog on every
    scheduled run instead of just what changed recently.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    now = datetime.now(UTC)
    _seed(storage, source_id, title="Ancient Item", analysis={}, fetched_at=now - timedelta(days=10))
    _seed(storage, source_id, title="Recent Item", analysis={}, fetched_at=now)
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=_fixed_decision("tutorial", 0.95)):
        result = runner.invoke(
            analyze_app, ["kinds", "--force", "--limit", "10", "--since-days", "5"]
        )

    assert result.exit_code == 0, result.output
    assert "Found 1" in result.output, result.output
    assert "Recent Item" in result.output
    assert "Ancient Item" not in result.output


# ---------------------------------------------------------------------------
# SC-2: merge into stored analysis, other fields untouched, classifier's own
# threshold reused rather than reimplemented
# ---------------------------------------------------------------------------


def test_kinds_merges_kind_and_confidence_leaving_other_analysis_fields_unchanged(
    local_env: Path,
) -> None:
    """
    SC-2: a classified item's stored analysis gains kind and kind_confidence, and
    every pre-existing analysis field survives unchanged.
    BREAKS: The command replaces the analysis dict wholesale instead of merging,
    dropping reading_summary/alpha_insights/etc that repair or the pipeline stored.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    content_id = _seed(
        storage,
        source_id,
        title="Release Notes For Foo",
        analysis={"reading_summary": "Foo shipped v2.", "alpha_insights": ["insight-1"]},
    )
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=_fixed_decision("release", 0.95)):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored = reread.get_content_by_id(content_id)
    reread.close()

    assert stored is not None
    analysis = stored["analysis"]
    assert analysis["kind"] == "release"
    assert analysis["kind_confidence"] == 0.95
    assert analysis["reading_summary"] == "Foo shipped v2."
    assert analysis["alpha_insights"] == ["insight-1"]


def test_kinds_merges_a_classified_item_is_never_selected_again(local_env: Path) -> None:
    """
    SC-2: once merged, the item carries a kind_confidence key and a second run finds
    nothing left to classify.
    BREAKS: The stored kind_confidence value (even when null) does not stop
    reselection, so the same item is billed and reclassified every run.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    _seed(storage, source_id, title="One Shot Item", analysis={})
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=_fixed_decision("release", 0.95)):
        first = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])
    assert first.exit_code == 0, first.output
    assert "Found 1" in first.output, first.output

    with patch(_PATCH_SUBMIT, side_effect=AssertionError("must never be called again")):
        second = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])

    assert second.exit_code == 0, second.output
    assert "No items need kind classification" in second.output, second.output


def test_kinds_merges_stores_null_kind_using_the_classifier_own_threshold(
    local_env: Path,
) -> None:
    """
    SC-2: a confidence exactly at CONFIDENCE_THRESHOLD classifies, one epsilon below
    it does not -- proving the command drives classify() itself rather than carrying
    a second, driftable copy of the threshold.
    BREAKS: A hardcoded threshold copy in analyze.py (e.g. a literal 0.7) silently
    diverges from kind_classifier.CONFIDENCE_THRESHOLD if that constant ever moves.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    at_threshold_id = _seed(storage, source_id, title="At Threshold Item", analysis={})
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=_fixed_decision("release", CONFIDENCE_THRESHOLD)):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])
    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored_at_threshold = reread.get_content_by_id(at_threshold_id)
    reread.close()
    assert stored_at_threshold is not None
    assert stored_at_threshold["analysis"]["kind"] == "release"
    assert stored_at_threshold["analysis"]["kind_confidence"] == CONFIDENCE_THRESHOLD

    storage = Storage(local_env)
    below_threshold_id = _seed(storage, source_id, title="Below Threshold Item", analysis={})
    storage.close()

    with patch(
        _PATCH_SUBMIT, side_effect=_fixed_decision("release", CONFIDENCE_THRESHOLD - 0.01)
    ):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])
    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored_below_threshold = reread.get_content_by_id(below_threshold_id)
    reread.close()
    assert stored_below_threshold is not None
    assert stored_below_threshold["analysis"]["kind"] is None
    assert stored_below_threshold["analysis"]["kind_confidence"] == pytest.approx(
        CONFIDENCE_THRESHOLD - 0.01
    )


# ---------------------------------------------------------------------------
# SC-3: per-item failure isolation, retry eligibility, final counts
# ---------------------------------------------------------------------------


def test_kinds_failure_continues_past_a_failed_item_and_leaves_it_retryable(
    local_env: Path,
) -> None:
    """
    SC-3: a classifier call that fails for one item does not abort the batch, and the
    failed item's analysis is left with no kind_confidence key so a later run retries
    it -- while the other item in the same batch is still classified.
    BREAKS: One failing item raises out of the loop and aborts every item after it,
    or the failed item is merged with a kind_confidence anyway and never retried.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    failing_id = _seed(storage, source_id, title="Boom Item", analysis={})
    ok_id = _seed(storage, source_id, title="Fine Item", analysis={})
    storage.close()

    def _fake(state: dict, *, service: str, model: str | None = None):
        if state["title"] == "Boom Item":
            raise RuntimeError("decisions endpoint unreachable")
        return _FakeDecisionCall(_kind_answer("release", 0.9))

    with patch(_PATCH_SUBMIT, side_effect=_fake):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    failed_item = reread.get_content_by_id(failing_id)
    ok_item = reread.get_content_by_id(ok_id)
    reread.close()

    assert failed_item is not None
    assert "kind_confidence" not in (failed_item["analysis"] or {}), (
        "SC-3: a failed item must be left retryable, not stamped with kind_confidence"
    )
    assert ok_item is not None
    assert ok_item["analysis"]["kind_confidence"] == 0.9


def test_kinds_failure_prints_classified_unclassified_and_failed_counts(
    local_env: Path,
) -> None:
    """
    SC-3: the run ends by printing counts of classified, unclassified, and failed
    items.
    BREAKS: The summary line silently drops the failed count, hiding that a rerun is
    needed to pick up the items that never got a kind stored.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    _seed(storage, source_id, title="Classified Item", analysis={})
    _seed(storage, source_id, title="Unclassified Item", analysis={})
    _seed(storage, source_id, title="Failed Item", analysis={})
    storage.close()

    def _fake(state: dict, *, service: str, model: str | None = None):
        if state["title"] == "Classified Item":
            return _FakeDecisionCall(_kind_answer("release", 0.95))
        if state["title"] == "Unclassified Item":
            return _FakeDecisionCall(_kind_answer("release", 0.1))
        raise RuntimeError("decisions endpoint unreachable")

    with patch(_PATCH_SUBMIT, side_effect=_fake):
        result = runner.invoke(analyze_app, ["kinds", "--force", "--limit", "10"])

    assert result.exit_code == 0, result.output
    assert "Classified" in result.output and "1" in result.output, result.output
    assert "Unclassified" in result.output, result.output
    assert "Failed" in result.output, result.output


# ---------------------------------------------------------------------------
# SC-4: refuses before any decisions call -- no kind_service, or remote mode
# ---------------------------------------------------------------------------


def test_kinds_refuses_when_kind_service_not_configured(
    local_env_no_kind_service: Path,
) -> None:
    """
    SC-4: with no [llm] kind_service configured, the command exits non-zero before
    ever reaching the decisions endpoint, and names kind_service in its message.
    BREAKS: The command falls through to KindClassifier construction with an empty
    service name, reaching submit_decision and failing with an opaque provider error
    instead of a clear configuration message.
    """
    with patch(_PATCH_SUBMIT, side_effect=AssertionError("must never be called")):
        result = runner.invoke(analyze_app, ["kinds", "--force"])

    assert result.exit_code != 0
    assert "kind_service" in result.output, result.output


def test_kinds_refuses_in_remote_mode_naming_local_mode_like_repair(
    isolated_xdg_env: Path,
) -> None:
    """
    SC-4: in remote mode the command exits non-zero before any decisions call, using
    the same local-mode guard (and message shape) `analyze repair` uses.
    BREAKS: `analyze kinds` grows its own remote-mode check with a drifted message
    instead of reusing `_check_local_mode`, the way `analyze status`/`repair` do.
    """
    _write_config(isolated_xdg_env, kind_service="prismis-openrouter-kind", remote_url="http://example.com")

    with patch(_PATCH_SUBMIT, side_effect=AssertionError("must never be called")):
        result = runner.invoke(analyze_app, ["kinds", "--force"])

    assert result.exit_code != 0
    assert "analyze kinds" in result.output
    assert "requires local daemon access" in result.output, result.output


# ---------------------------------------------------------------------------
# Confirmation gate (stakes): declining aborts without spending anything
# ---------------------------------------------------------------------------


def test_kinds_declining_confirmation_skips_classification(local_env: Path) -> None:
    """
    Declining the pre-processing confirmation (no --force) makes no decisions call.
    BREAKS: The confirmation prompt is cosmetic and the batch runs regardless of the
    operator's answer.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    content_id = _seed(storage, source_id, title="Should Not Be Classified", analysis={})
    storage.close()

    with patch(_PATCH_SUBMIT, side_effect=AssertionError("must never be called")):
        result = runner.invoke(analyze_app, ["kinds"], input="n\n")

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    item = reread.get_content_by_id(content_id)
    reread.close()

    assert item is not None
    assert "kind_confidence" not in (item["analysis"] or {}), (
        "declining the confirmation must leave the item unclassified"
    )


# ---------------------------------------------------------------------------
# SC-10 (dedup work order, job 10, cluster 13): `analyze repair` builds its
# analysis dict through build_llm_analysis, the same daemon-side helper the
# fetch pipeline uses, and passes learned preferences into the evaluator the
# same way orchestrator.run_once does.
# ---------------------------------------------------------------------------


def _fake_complete_result(payload: dict[str, Any]) -> MagicMock:
    """Stand in for llm_call.complete()'s CompleteResult, JSON-encoding payload
    into .text the way summarizer.py/evaluator.py's extract_json() expects."""
    fake = MagicMock()  # claudex-guard: allow-mock
    fake.text = json.dumps(payload)
    fake.tokens.input = 10
    fake.tokens.output = 5
    fake.cost = 0.0
    fake.model = "gpt-4.1-mini"
    fake.duration_ms = 1
    return fake


_SUMMARY_PAYLOAD: dict[str, Any] = {
    "summary": "A short summary.",
    "reading_summary": "# Title\n\n## Overview\nSomething happened.",
    "alpha_insights": ["insight one"],
    "patterns": ["pattern one"],
    "quotes": [],
    "tools": [],
    "urls": [],
}

_EVAL_PAYLOAD: dict[str, Any] = {
    "priority": "medium",
    "matched_interests": ["topic"],
    "reasoning": "matches topic",
}


def _repair_llm_side_effect() -> list[MagicMock]:
    """One repair pass over a single item calls complete() twice: once from
    summarizer.summarize_with_analysis, once from evaluator.evaluate_content --
    in that order (analyze.py's repair() Step 1 then Step 2)."""
    return [
        _fake_complete_result(_SUMMARY_PAYLOAD),
        _fake_complete_result(_EVAL_PAYLOAD),
    ]


def test_repair_includes_preference_influenced_and_passes_learned_preferences_to_evaluator(
    local_env: Path,
) -> None:
    """
    SC-10: with >=5 recent feedback votes, repair fetches learned preferences and
    threads them into evaluate_content -- proven by the stored analysis carrying
    preference_influenced=True, a flag ContentEvaluator only sets when it actually
    received a non-empty learned_preferences argument (evaluator.py's
    evaluate_content, not something the fake LLM response itself supplies).
    BREAKS: repair building its own analysis dict inline (its pre-cluster-13 shape)
    never threads learned_preferences into evaluate_content, so
    preference_influenced stays False even with plenty of recent votes.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    # 5 votes in the last 30 days crosses get_learned_preferences' min_votes.
    for i in range(5):
        voted_id = _seed(storage, source_id, title=f"Voted Item {i}")
        storage.update_content_status(voted_id, user_feedback="up")

    target_id = _seed(
        storage, source_id, title="Needs Repair", summary=None, analysis=None
    )
    storage.close()

    with patch(
        _PATCH_COMPLETE, side_effect=_repair_llm_side_effect()
    ):  # claudex-guard: allow-mock
        result = runner.invoke(analyze_app, ["repair", "--force", "--limit", "1"])

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored = reread.get_content_by_id(target_id)
    reread.close()

    assert stored is not None
    assert stored["analysis"]["preference_influenced"] is True, stored["analysis"]


def test_repair_still_writes_preference_influenced_false_when_no_learned_preferences(
    local_env: Path,
) -> None:
    """
    SC-10: with no recent feedback votes, repair's stored analysis still carries
    the preference_influenced key (as False) -- the fetch pipeline always writes
    this key, and matching that shape is cluster 13's fix, not swapping one
    omission (no key) for another (a key present only sometimes).
    BREAKS: repair reverting to its pre-fix narrower dict drops the key entirely
    instead of writing it as False.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    target_id = _seed(
        storage, source_id, title="Needs Repair Too", summary=None, analysis=None
    )
    storage.close()

    with patch(
        _PATCH_COMPLETE, side_effect=_repair_llm_side_effect()
    ):  # claudex-guard: allow-mock
        result = runner.invoke(analyze_app, ["repair", "--force", "--limit", "1"])

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored = reread.get_content_by_id(target_id)
    reread.close()

    assert stored is not None
    assert "preference_influenced" in stored["analysis"], stored["analysis"]
    assert stored["analysis"]["preference_influenced"] is False, stored["analysis"]


# ---------------------------------------------------------------------------
# SC-3: `analyze repair` sets title_only through the shared build_llm_analysis
# helper too -- not just the daemon pipeline.
# ---------------------------------------------------------------------------


def test_repair_sets_title_only_true_for_unreadable_content(local_env: Path) -> None:
    """
    SC-3: repairing an item whose stored content fails the shared readability
    check stores title_only: true, even though it is still fully summarised,
    prioritised and stored.
    BREAKS: repair building its analysis dict without threading the item's
    content into build_llm_analysis leaves title_only unset or always False,
    regardless of what was actually stored.
    """
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    target_id = _seed(
        storage,
        source_id,
        title="Needs Repair, No Real Content",
        summary=None,
        analysis=None,
        content="No content available",
    )
    storage.close()

    with patch(
        _PATCH_COMPLETE, side_effect=_repair_llm_side_effect()
    ):  # claudex-guard: allow-mock
        result = runner.invoke(analyze_app, ["repair", "--force", "--limit", "1"])

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored = reread.get_content_by_id(target_id)
    reread.close()

    assert stored is not None
    assert stored["analysis"]["title_only"] is True, stored["analysis"]


def test_repair_sets_title_only_false_for_readable_content(local_env: Path) -> None:
    """SC-3 companion: an item with genuine content is repaired with
    title_only: false, proving the flag isn't hardcoded true."""
    storage = Storage(local_env)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    target_id = _seed(
        storage,
        source_id,
        title="Needs Repair, Real Content",
        summary=None,
        analysis=None,
        content=(
            "Researchers published a new decade-long study of patient records "
            "this week, finding a pattern that held after controlling for age, "
            "income, and prior health history."
        ),
    )
    storage.close()

    with patch(
        _PATCH_COMPLETE, side_effect=_repair_llm_side_effect()
    ):  # claudex-guard: allow-mock
        result = runner.invoke(analyze_app, ["repair", "--force", "--limit", "1"])

    assert result.exit_code == 0, result.output

    reread = Storage(local_env)
    stored = reread.get_content_by_id(target_id)
    reread.close()

    assert stored is not None
    assert stored["analysis"]["title_only"] is False, stored["analysis"]
