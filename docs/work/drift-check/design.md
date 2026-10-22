---
type: design
date: 2026-10-16
title: "Design: drift-check"
description: "Make prismis notice its own scoring going quiet: every cycle measures the unprioritized share of the last 24 hours against its usual level, keeps that measurement, and shows a warning where the user reads (TUI header, web status bar, verify) while the share sits well above usual, so a regression like #96's is seen within a day by prismis instead of after two weeks by the user."
purpose: "The recorded design of the drift-check work order, read by its plan and its builders."
producer: cli:shape
---

# Design: drift-check

## Purpose

Make prismis notice its own scoring going quiet: every cycle measures the unprioritized share of the last 24 hours against its usual level, keeps that measurement, and shows a warning where the user reads (TUI header, web status bar, verify) while the share sits well above usual, so a regression like #96's is seen within a day by prismis instead of after two weeks by the user.

## Form

1. The check. A new drift module computes one DriftCheck from storage at a given time: the window is items whose created_at falls in the trailing 24 hours; its share is how many of them have a null priority. The baseline is the median of the shares of the 28 previous 24-hour windows that each held at least 20 items, and needs at least 14 such windows. The check flags when the window holds at least 20 items, a baseline exists, and share minus baseline is at least [drift] threshold (default 0.20). While the latest stored check is flagged, the next check does not recompute the baseline: it carries that check's baseline forward, and a flagged check clears only when the share falls below that pinned baseline plus half the threshold, so a regression that persists stays flagged instead of clearing once it fills half of a recomputed 28-day window, and one that sits near the threshold does not flap the warning (on <host>'s backup: one flag for 336 hours instead of 12 flag/clear flips). Without enough items or history it records why it did not judge, never a silent pass. Every newly scored item counts; title-only items are not excluded, because on <host> the two shares never differed.

2. The record. A drift_checks table (schema.sql plus a migration in database.py's _MIGRATIONS) stores each check: checked_at, window item count, unprioritized count, share, baseline, baseline window count, deviation, the threshold used, flagged, and the reason when it did not judge. Rows are never updated. The same check is written to obs_log as drift.check.

3. The trigger. run_once runs the check at the end of each cycle and stores it; when it flags, the cycle's console output says so with the two shares.

4. Reading it. Storage gains latest_drift_check(). GET /api/drift returns the latest check (null before the first). `prismis-daemon verify` prints the latest check and marks a flagged one.

5. Showing it (job 2). While the latest check is flagged, the web status bar shows a warning naming the measured and usual shares, and the TUI header shows the same line, read from GET /api/drift in remote mode and from drift_checks in local mode. A check that stops flagging clears the warning on the next refresh.

6. The replay test. A fixture holds <host>'s hourly item and null counts for 2026-01-05..2026-10-04, extracted from the pre-re-score backup (counts only, no titles or content). The test seeds a database with one item per counted row and runs the check every two hours across that span: no flag before 2026-09-30T17:00Z, a flag no later than 2026-10-01T17:00Z, and the flag still standing at 2026-10-04 while the share stays near 0.80.

## Commitments

- Every daemon cycle records one drift check with the measured share, the baseline, the deviation and the decision, including why it did not judge when items or history are too few.
- The check flags when the trailing-24-hour unprioritized share is at least the configured threshold (default 0.20) above the median of the previous 28 daily shares, with at least 20 items in the window and 14 qualifying baseline windows.
- Replaying <host>'s 2026-01-05..2026-10-04 counts, the check raises no flag before 2026-09-30T17:00Z, raises one no later than 2026-10-01T17:00Z, and keeps it through 2026-10-04.
- The latest check is readable through GET /api/drift and `prismis-daemon verify`.
- While the latest check is flagged, the TUI header and the web status bar say so with the measured and usual shares, and the warning clears when a later check stops flagging.
- While flagged, the check compares against the baseline it flagged with and clears only below that baseline plus half the threshold, so a persisting regression is neither absorbed into a recomputed baseline nor flapping.

## Sacrifices

- The threshold is set from one user's nine months; normal weekend source-mix swings reached +0.176 against the +0.20 threshold, so another user's mix may need a different value, which is why it is a config value and every deviation is recorded.
- A regression smaller than 20 points, or one that builds gradually enough to move the 28-day baseline before it crosses the threshold, is not flagged.
- No push notification: the desktop notifier cannot reach a Linux-hosted daemon's user, and adding a push service is out of this work.
- Detection is on the share of null priorities only; a regression that shifts HIGH to MEDIUM without changing nulls is not seen.

## Risk

If the threshold is too tight for a user's source mix, the warning flaps on quiet weekends and the user learns to ignore it, which is the same failure as no check; the recorded deviations make that visible and the config value fixes it. If drift_checks grows unbounded, it adds about 12 rows a day, which is negligible for years.

## Licensed by

.specify/memory/constitution.md:55
