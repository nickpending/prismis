"""Shared analysis-dict assembly for the daemon pipeline and `analyze repair`.

dedup-triage.md's cluster 13: cli/analyze.py's `repair` command and
orchestrator.py's `fetch_source_content` each hand-built the same seven
summary_result fields plus two evaluation fields into an analysis dict, and
had drifted apart. This module is the one home for that dict; the
merge-with-existing-analysis step (which differs in width between the two
callers) stays with each caller, not here.
"""

from typing import Any

from .evaluator import ContentEvaluation
from .readability import (
    CONTENT_BASIS_DISCUSSION,
    is_discussion_basis,
    readability_failure,
)
from .summarizer import ContentSummary


def content_basis(content: str | None) -> str | None:
    """What the readable text of `content` rests on: `"discussion"` when the article
    is absent and only the item's discussion carries prose, else None. The one
    producer -- the summarizer's request and the stored analysis both read it."""
    return CONTENT_BASIS_DISCUSSION if is_discussion_basis(content) else None


def title_only_reason(
    content: str | None,
    fetch_outcome: dict[str, Any] | None,
    substantive: bool | None,
) -> str | None:
    """Why an item is title-only, or None when it is not -- the one producer.

    `content:<rule>` when the readability check fails (prefixed
    `fetch_failed:<detail>; ` when the fetcher's extraction outcome was a failed
    fetch); else `model:not_substantive` when the light model judged readable
    content not substantive; else None. An unknown (None) verdict never counts
    as not substantive.
    """
    rule = readability_failure(content)
    if rule is not None:
        reason = f"content:{rule}"
        if isinstance(fetch_outcome, dict) and (
            fetch_outcome.get("outcome") == "fetch_failed"
        ):
            reason = f"fetch_failed:{fetch_outcome.get('detail') or ''}; {reason}"
        return reason
    if substantive is False:
        return "model:not_substantive"
    return None


def evaluation_fields(evaluation: ContentEvaluation) -> dict[str, Any]:
    """The analysis keys an evaluation owns -- the one producer, shared by the
    full pass (build_llm_analysis) and `analyze priority`, which re-evaluates
    without re-summarizing."""
    return {
        "matched_interests": evaluation.matched_interests,
        "priority_reasoning": evaluation.reasoning,
    }


def build_llm_analysis(
    summary_result: ContentSummary,
    evaluation: ContentEvaluation,
    content: str | None,
    fetch_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the analysis dict written after a summarize+evaluate pass.

    Args:
        summary_result: Output of ContentSummarizer.summarize_with_analysis
        evaluation: Output of ContentEvaluator.evaluate_content
        content: The raw content the item was summarized/evaluated from --
            used only to set `title_only` and `title_only_reason` (SC-3);
            neither caller computes those itself.
        fetch_outcome: The fetcher's `{"outcome", "detail"}` record for the
            extraction it attempted, when it recorded one.

    Returns:
        The analysis dict both the daemon pipeline and `analyze repair` store.
        Always includes `title_only`,
        true when `title_only_reason` yields a reason (gh #80), and
        `title_only_reason` (None when readable and substantive). Records
        `content_basis` = "discussion" when the readable text is the discussion
        only (the article being absent), and no `content_basis` key otherwise.
    """
    reason = title_only_reason(content, fetch_outcome, summary_result.substantive)
    analysis: dict[str, Any] = {
        "reading_summary": summary_result.reading_summary,
        "alpha_insights": summary_result.alpha_insights,
        "patterns": summary_result.patterns,
        "quotes": summary_result.quotes,
        "tools": summary_result.tools,
        "urls": summary_result.urls,
        **evaluation_fields(evaluation),
        "metadata": summary_result.metadata,
        "title_only": reason is not None,
        "title_only_reason": reason,
    }
    basis = content_basis(content)
    if basis:
        analysis["content_basis"] = basis
    bounded = summary_result.metadata.get("content_bounded")
    if bounded:
        analysis["content_bounded"] = bounded
    return analysis
