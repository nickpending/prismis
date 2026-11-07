---
type: design
date: 2026-11-07
title: "Design: source-health-screen"
description: "The sources screen is where the operator reads why a source is flagged and acts on it; it has to keep the source they are on, and the reason it is flagged, in view at any terminal size."
purpose: "The recorded design of the source-health-screen work order, read by its plan and its builders."
producer: cli:shape
---

# Design: source-health-screen

## Purpose

The sources screen is where the operator reads why a source is flagged and acts on it; it has to keep the source they are on, and the reason it is flagged, in view at any terminal size.

## Form

The sources screen draws its list itself, as rows it has already fitted to its width, and scrolls them by the cursor.

1. Size. The help screen's sizing rule (help_modal.go line 27: 75 percent of the terminal's width and its height less 8, at least 50 by 20, never wider than the terminal less 4) moves into one modalSize(width, height) function in modal.go; HelpModal.SetSize and SourceModal.SetSize (source_modal.go line 195) both call it, so the two modals cannot drift. The list's width is the modal's less its padding (4) and its height the modal's less the frame, header and status bar (7), the arithmetic SetSize already uses.

2. Rows. renderListContentOnly (source_modal.go line 612) becomes listRows(theme, width), returning the list as rows that each fit in width, and for each source the index of its first and last row. A source's row is cut to width with ansi.Truncate of charmbracelet/x/ansi (already in the module graph, measuring display width); its evidence is wrapped to the width less the existing four-space indent with ansi.Wrap, which breaks a word longer than the width, so every row fits; each row is styled after wrapping so no style sequence spans a break. failureEvidence (source_modal.go line 116) drops maxEvidenceError and its cut: the whole stored error, through singleLine, which strips terminal controls and flattens it, is part of the evidence.

3. Window. The bubbles viewport leaves the sources screen: list mode no longer passes messages to it (source_modal.go line 444), so its own key map no longer scrolls a line per j or k. SourceModal keeps offset, the first visible row. windowStart(first, last, offset, height) is a pure function returning the offset that keeps rows first..last of the selected source inside height rows, moving it as little as possible (up to first when first is above, down so last is the bottom row when last is below, never past the last row's page). Update applies it after every cursor move, LoadSources, SetSize, error change and status change; View applies it again to the stored offset before drawing, so no path can show a window without the cursor, and draws exactly the rows offset..offset+height, padded to height. The height is the list height less the rows the error line takes, the error being wrapped to width with ansi.Wrap like evidence. A mouse wheel moves the cursor one source up or down, so wheel scrolling keeps working and keeps the cursor in view.

4. Status bar. The status message is cut with ansi.Truncate to one row of the status bar's width with an ellipsis, so a long message never adds rows to the screen.

Acts on the cause: The cause is a list of variable-height rows scrolled by a viewport whose own key map moves a line per keypress while the cursor moves a source, inside a 41-column box that wraps rows the offset never counts, and an evidence line cut to the error's first 60 runes. The form removes each condition: the viewport and its key map leave the list, the offset is derived from the selected source's own rows every time anything changes, every row is fitted to the width before it is counted so no wrap is hidden, the box takes the terminal's size, and the cut is deleted.

## Commitments

- After every keypress, resize, reload, error or status message the selected source's row and every row of its evidence are inside the drawn window, at any terminal size.
- Every row the list draws occupies exactly one terminal row; the window always draws as many list rows as its height holds while rows remain below it.
- A broken source's evidence carries its whole stored error, control-stripped and flattened, wrapped to the screen's width, with no cut and no matching against its wording.
- The sources screen sizes with the terminal by the help screen's rule, one function serving both.
- The daemon, its stored columns and the API are unchanged; the main view's source list is unchanged.

## Sacrifices

- A broken source's evidence repeats what its stored error carries, including the feed URL twice and httpx's documentation link, so a 404 takes about two rows at a 188-column terminal. (1 of 49 sources on the operator's daemon host whose stored state is broken (the inactive, error_count 5 row, NCC Group))
- The box follows the help screen's minimum of 20 rows, so on a terminal shorter than 28 rows it is taller than the terminal less 8, as the help screen already is. (0 of 1 the operator's TUI pane)
- A mouse wheel moves the cursor rather than scrolling the list past it. (not a case: How the wheel behaves on this screen is a property of the design, not a case a use of prismis runs into.)

## Risk

If the row spans are miscounted (a row wider than the width, or a style sequence across a wrap), the window can again cut the cursor's rows; the criteria drive the real screen at the walk's size with the walk's sources and assert the selected row on screen after every step, so a miscount fails there. If the help screen's sizing changes later, both modals change together, which is the intent.

## Licensed by

tui/internal/ui/help_modal.go:27
