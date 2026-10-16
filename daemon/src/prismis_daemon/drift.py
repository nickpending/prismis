"""Drift check: has the unprioritized share of new items moved well off its usual level?

Every daemon cycle measures the share of items created in the trailing 24 hours that
have a null priority, compares it with the median of the shares of the 28 previous
24-hour windows, and stores the decision. A regression in scoring (a model swap, a
prompt edit, an over-applying carve-out) shows up as that share rising and staying up.

The decision (`decide`) is pure; `run_drift_check` reads the windows from storage,
decides, stores the row and logs the `drift.check` event.
"""

import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .observability import log as obs_log
from .storage import Storage

BASELINE_WINDOWS = 28
MIN_WINDOW_ITEMS = 20
MIN_BASELINE_WINDOWS = 14
DEFAULT_THRESHOLD = 0.20

# Shares are fractions; a deviation computed from two of them carries float noise that
# would move a check sitting exactly on a boundary (0.5 - 0.3 is 0.19999999999999996).
_DEVIATION_DIGITS = 10


@dataclass(frozen=True)
class DriftCheck:
    """One drift check: the measurement, the baseline it used, and the decision.

    `deviation` is None when the check did not judge, and `reason` then names each
    minimum that was not met. `baseline_windows` is how many qualifying daily windows
    stood behind the baseline.
    """

    checked_at: str
    window_items: int
    window_null: int
    share: float | None
    baseline: float | None
    baseline_windows: int
    deviation: float | None
    threshold: float
    flagged: bool
    reason: str | None


def decide(
    *,
    checked_at: str,
    windows: list[tuple[int, int]],
    threshold: float,
    previous: dict[str, Any] | None,
) -> DriftCheck:
    """Decide one check from the window counts and the latest stored check.

    Args:
        checked_at: ISO 8601 time of the check.
        windows: (items, null-priority items) per 24-hour window, newest first; index 0
            is the trailing window being judged, the rest are the baseline candidates.
        threshold: Share difference at which the check flags.
        previous: The latest stored check as a dict, or None.

    A flagged previous check pins its baseline: this check compares against that
    baseline instead of recomputing one, stays flagged while the share is at least
    half the threshold above it, and clears below that. An unflagged previous check
    recomputes the baseline and flags at the full threshold.

    A check that cannot judge (too few items, too little history) is not a judgement,
    so it does not clear a standing flag: while the previous check was flagged it stays
    flagged and keeps the pinned baseline, with the reason saying it did not judge.
    Without a standing flag it is not flagged.
    """
    window_items, window_null = windows[0]
    share = window_null / window_items if window_items else None

    pinned_baseline: float | None = None
    pinned_windows = 0
    if previous is not None and previous["flagged"] and previous["baseline"] is not None:
        pinned_baseline = float(previous["baseline"])
        pinned_windows = int(previous["baseline_windows"])
    pinned = pinned_baseline is not None

    baseline: float | None
    if pinned_baseline is not None:
        baseline, baseline_windows = pinned_baseline, pinned_windows
    else:
        shares = [
            nulls / items for items, nulls in windows[1:] if items >= MIN_WINDOW_ITEMS
        ]
        baseline_windows = len(shares)
        baseline = statistics.median(shares) if shares else None
        if baseline_windows < MIN_BASELINE_WINDOWS:
            baseline = None

    missing = []
    if window_items < MIN_WINDOW_ITEMS:
        missing.append(
            f"window holds {window_items} items, fewer than the {MIN_WINDOW_ITEMS} needed"
        )
    if baseline is None:
        missing.append(
            f"{baseline_windows} qualifying daily windows of history, "
            f"fewer than the {MIN_BASELINE_WINDOWS} needed"
        )
    if missing or share is None or baseline is None:
        return DriftCheck(
            checked_at=checked_at,
            window_items=window_items,
            window_null=window_null,
            share=share,
            baseline=baseline,
            baseline_windows=baseline_windows,
            deviation=None,
            threshold=threshold,
            flagged=pinned,
            reason="not judged: " + "; ".join(missing),
        )

    deviation = round(share - baseline, _DEVIATION_DIGITS)
    flagged = deviation >= (threshold / 2 if pinned else threshold)
    return DriftCheck(
        checked_at=checked_at,
        window_items=window_items,
        window_null=window_null,
        share=share,
        baseline=baseline,
        baseline_windows=baseline_windows,
        deviation=deviation,
        threshold=threshold,
        flagged=flagged,
        reason=None,
    )


def run_drift_check(
    storage: Storage, threshold: float, now: datetime | None = None
) -> DriftCheck:
    """Measure, decide, store and log one drift check; return it.

    A storage failure raises: the caller decides whether the cycle survives it.
    """
    moment = now or datetime.now(UTC)
    check = decide(
        checked_at=moment.isoformat(),
        windows=storage.drift_window_counts(moment, 1 + BASELINE_WINDOWS),
        threshold=threshold,
        previous=storage.latest_drift_check(),
    )
    storage.insert_drift_check(asdict(check))
    obs_log("drift.check", **asdict(check))
    return check
