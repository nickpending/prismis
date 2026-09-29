"""Shared analysis-dict assembly for the daemon pipeline and `analyze repair`.

dedup-triage.md's cluster 13: cli/analyze.py's `repair` command and
orchestrator.py's `fetch_source_content` each hand-built the same seven
summary_result fields plus two evaluation fields into an analysis dict, and
had drifted apart -- repair's dict omitted `preference_influenced` and never
passed learned preferences into the evaluator at all. This module is the one
home for that dict; the merge-with-existing-analysis step (which differs in
width between the two callers) stays with each caller, not here.

Fixing the second drift (repair now passes learned preferences to the
evaluator, matching orchestrator.run_once) means both callers now also need
the same "at least min_votes recent votes" gate on
Storage.get_feedback_statistics -- get_learned_preferences below is that one
mechanical fetch, so this fix does not itself become a new clone between
orchestrator.py and analyze.py. Each caller keeps its own logging/error
handling around the call, which is why it returns total_votes too rather than
printing anything itself.
"""

from typing import Any

from .evaluator import ContentEvaluation
from .storage import Storage
from .summarizer import ContentSummary


def get_learned_preferences(
    storage: Storage, since_days: int = 30, min_votes: int = 5
) -> tuple[str | None, int]:
    """Fetch the learned-preferences context string for LLM evaluation.

    Args:
        storage: Storage instance to read feedback statistics from
        since_days: Lookback window for feedback statistics
        min_votes: Minimum total votes in the window before preferences apply

    Returns:
        (learned_preferences, total_votes) -- learned_preferences is
        storage.get_feedback_statistics(since_days)'s for_llm_context field,
        or None when fewer than min_votes votes exist in the window;
        total_votes is always returned so a caller can report it.
    """
    feedback_stats = storage.get_feedback_statistics(since_days=since_days)
    total_votes = feedback_stats.get("totals", {}).get("total_votes", 0)
    if total_votes < min_votes:
        return None, total_votes
    return feedback_stats.get("for_llm_context"), total_votes


def build_llm_analysis(
    summary_result: ContentSummary, evaluation: ContentEvaluation
) -> dict[str, Any]:
    """Assemble the analysis dict written after a summarize+evaluate pass.

    Args:
        summary_result: Output of ContentSummarizer.summarize_with_analysis
        evaluation: Output of ContentEvaluator.evaluate_content

    Returns:
        The analysis dict both the daemon pipeline and `analyze repair` store.
        Always includes `preference_influenced` from `evaluation` -- the
        daemon pipeline always did; `analyze repair` previously did not,
        which this consolidation fixes (SC-10).
    """
    return {
        "reading_summary": summary_result.reading_summary,
        "alpha_insights": summary_result.alpha_insights,
        "patterns": summary_result.patterns,
        "quotes": summary_result.quotes,
        "tools": summary_result.tools,
        "urls": summary_result.urls,
        "matched_interests": evaluation.matched_interests,
        "priority_reasoning": evaluation.reasoning,
        "preference_influenced": evaluation.preference_influenced,
        "metadata": summary_result.metadata,
    }
