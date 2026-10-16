---
type: design
date: 2026-10-16
title: "Design: drift-warning"
description: "Put the drift check where the user reads: while the daemon's latest drift check is flagged, the TUI header and the web status bar carry one warning line naming the measured and usual unprioritized shares, and the line disappears on the first refresh after a check stops flagging, so a scoring regression is seen in the feed the user already has open rather than in verify or the API."
purpose: "The recorded design of the drift-warning work order, read by its plan and its builders."
producer: cli:shape
---

# Design: drift-warning

## Purpose

Put the drift check where the user reads: while the daemon's latest drift check is flagged, the TUI header and the web status bar carry one warning line naming the measured and usual unprioritized shares, and the line disappears on the first refresh after a check stops flagging, so a scoring regression is seen in the feed the user already has open rather than in verify or the API.

## Form

1. The warning text. One line, the same on both surfaces and the same as the daemon's console line: '⚠ Drift: <share>% of the last 24 hours' items are unprioritized, usual is <baseline>%'. A flagged check carried through a thin window has no share and may have no baseline; the line then says 'too few items to measure' for the share and '?' for the baseline, exactly as the daemon prints it (orchestrator.py's _record_drift_check). A check that is not flagged, no check at all, or a failed read all render nothing.

2. The web view. loadContent fetches GET /api/drift beside the entries on every run, including the 30-second auto-refresh, and keeps the latest check on the app; updateStatus, which rebuilds #statusInfo from the loaded content, appends a 'status-drift' span with the warning text when that check is flagged and nothing otherwise, so the warning refreshes and clears on the same cycle the counts do. A failed /api/drift fetch clears the held check and leaves the feed loading as before. The offline path (updateOfflineStatus) is unchanged: it already replaces the whole status bar.

3. The TUI read. The db package gains a DriftCheck type (checked_at, share, baseline, flagged, reason; share and baseline nullable) and LatestDriftCheck(), the drift_checks highest-id row on the CountUpvotedItems GetDB/QueryRow pattern. The api package gains GetDrift() on the GetSources pattern, decoding GET /api/drift's data (null before the first check) into the same db.DriftCheck, the way api.ContentItem is converted to db.ContentItem for the list. The Model holds the latest check; the initial load and buildRefreshCmd in both modes read it beside the items and carry it on itemsLoadedMsg, so it refreshes with the feed on manual and auto refresh. A read error leaves the check nil: no warning, and the feed loads exactly as it does today.

4. The TUI header. feed.go's header line gains the warning text between ' PRISMIS' and the view-state/clock segment while the held check is flagged, inside the same single gradient line and truncated to the width, so the 5-row reservation (header, blank, status, command, borders) is untouched and no view shifts. Not flagged, the header is byte-identical to today.

5. Proof. A Chromium test on the real page seeds a flagged drift_checks row and asserts the warning span's text in #statusInfo, then seeds a later unflagged row and asserts it is gone after a refresh; a sibling asserts the thin-window wording. A Go view test renders the list view from a Model with a flagged check and asserts the header holds the warning text, from an unflagged check and asserts the header is unchanged, and from a nil check likewise; api and db tests cover GetDrift's null data and LatestDriftCheck's empty-table case as distinct from a flagged row.

## Commitments

- While the latest drift check is flagged, the TUI header and the web status bar show one warning line naming the measured and usual unprioritized shares, with the thin-window wording when the share or baseline is missing.
- The warning clears on the first refresh after a later check stops flagging, on both surfaces.
- The TUI reads the latest check from GET /api/drift in remote mode and from the drift_checks table in local mode, on the same refresh that reloads items.
- A missing or failed drift read never blocks or alters the feed: no check, an unflagged check and a read error all render no warning.
- Unflagged, both surfaces render exactly what they render today.

## Sacrifices

- The warning is only as current as the surface's own refresh: 30 s on the web, the TUI's auto-refresh interval or a manual refresh in the TUI; nothing pushes it.
- The TUI shows the warning only in the list view's header; the reader and other views do not repeat it.
- No link from the warning to the check's history; the deviation and threshold stay in GET /api/drift and verify.

## Risk

If the header line is wider than the terminal while flagged, the warning truncates the view-state and clock segment rather than wrapping; the line is kept to the title, the warning and the state, and truncation is to the right so the warning itself survives. If the web page holds a stale flagged check after the daemon is restarted on an older build without /api/drift, the next fetch fails and clears it.

## Licensed by

docs/work/drift-check/shape/design.md:26
