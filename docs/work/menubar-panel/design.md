---
type: design
date: 2026-10-28
title: "Design: menubar-panel"
description: "Make the menu-bar app usable at real volume (about 20 HIGH items a day, thousands unread): a quiet icon that shows a dot when HIGH items were fetched since the operator last looked, and a designed panel of the newest five, with no notification banners."
purpose: "The recorded design of the menubar-panel work order, read by its plan and its builders."
producer: cli:shape
---

# Design: menubar-panel

## Purpose

Make the menu-bar app usable at real volume (about 20 HIGH items a day, thousands unread): a quiet icon that shows a dot when HIGH items were fetched since the operator last looked, and a designed panel of the newest five, with no notification banners.

## Form

Built on the shipped macos/ package (menubar-mac); the config reader, API client transport, item link, login toggle, build script, make target and gate wiring stay.

1. Last looked. PrismisCore's AlertedStore is replaced by a LastLookedStore: one ISO-8601 time in ~/Library/Application Support/Prismis/last-looked.json, written atomically (temporary file and rename, as AlertedStore did). The stored time is always a daemon fetched_at, never the Mac's clock, so the two machines' clocks never meet. With no file, the first poll stores the newest fetched_at it saw (or leaves the file absent while the daemon lists nothing), so a fresh install starts quiet. Opening the panel stores the newest fetched_at among the rows and the new items the panel holds.

2. The poll. Every five minutes and on Refresh, the Poller makes two requests to GET /api/entries with the key: priority=high&unread_only=true&view=list&sort_by=date&limit=5 for the rows, and the same with since=<last looked> and limit=100 for what is new. The snapshot holds the five rows, the new count (the second request's item count, shown as "99+" past 99), each row's isNew (its fetched_at after last looked), the check time, and a failure that keeps the last good rows and count. Each check is logged with its counts or its failure. APIClient's Item gains kind (from the list view's projected analysis), summary, fetched_at and published_at, decoded tolerantly so a missing kind or summary is nil, not a failed poll.

3. The icon. Quiet glyph when the new count is 0; the same glyph with an accent dot when it is above 0; dimmed with an amber mark when the last check failed or the config was refused.

4. The panel. MenuBarExtra with .menuBarExtraStyle(.window), a 380 pt wide SwiftUI panel in the CleanCyber palette as on the canvas mockup: header with the new count in mono accent, "new since you last looked", and "checked Nm ago"; up to five rows, each a button with a dot when new, the title clamped to two lines (brighter when new), a meta line of source, a kind chip (vulnerability, experience, research, release, news and the other kinds the daemon names, each with its own tint; none when nil) and age from published_at (else fetched_at), and the summary revealed on hover; a row click opens the item's #item link. Failure replaces the header line with the reason (refused key, unreachable, server error) and keeps the rows. Footer: Open prismis (the daemon's base url in the browser), Refresh now, and a settings button whose menu holds Launch at Login and Quit. Opening the panel marks looked-at after it renders, so the dots show for this look and clear for the next.

5. Removed: Notifier, NotificationClicks, the UNUserNotificationCenter delegate, AlertedStore and their tests; Info.plist keeps no notification usage.

Revised after delivery, both at rudy's word: ec95b76 drew the panel natively instead of in the CleanCyber palette of the mockup (the window's own material, light and dark mode, system type and label colours, SF Symbols, the user's accent colour for the new count, the new dots and the icon's dot, and an SF Symbol icon); 0ab23d8 removed the summary that appeared on hovering a row, made every icon state a template image, and added retries 10, 30 and 60 seconds after a launch-time check fails.

## Commitments

- The app posts no notifications.
- The icon shows a dot exactly when the daemon reports HIGH unread items fetched after the last time the operator opened the panel, and none on a fresh install's first check.
- Opening the panel moves the last-looked time to the newest fetched_at the panel holds, so the next check shows no dot until something fetched later arrives.
- The panel lists the newest five unread HIGH items with two-line titles, source, a kind chip when the item has a kind, and age; items fetched since the last look are marked; hovering a row shows its summary (dropped in 0ab23d8; the hover highlight stays); clicking it opens its web detail panel.
- The header shows the new count (99+ past 99) and when the app last checked; a failed check shows its reason there, keeps the last rows and dims the icon with an amber mark.
- The footer opens the daemon's web view, refreshes now, and holds Launch at Login and Quit.
- The last-looked time survives a restart and is written atomically.

## Sacrifices

- No banners at all: a HIGH item reaches the operator only when they glance at the icon.
- The panel shows five items; the rest are one click away in the web view.
- The new count stops at 100 per check, shown as 99+.
- The panel's look is verified by eye against the mockup, not by a test.

## Risk

If last-looked is written before the panel's rows render, the dots never show; the order is fixed in the form and its test opens, then reads the stored time. More than 100 new items in one look leaves the oldest of them new for the next look; the 99+ count says so.

## Licensed by

daemon/src/prismis_daemon/storage.py:1092
