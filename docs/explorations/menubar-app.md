---
type: exploration
date: 2026-10-27
title: "Mac menu-bar app for prismis"
description: "Replacing the daemon's terminal-notifier alerts with a Mac menu-bar app that shows and alerts on new HIGH items from a local or remote daemon."
purpose: "The record of how the menu-bar app's shape was decided, read when its issues are shaped."
producer: skill
---

# Mac menu-bar app for prismis

## Problem

prismis tells its user about new HIGH-priority items through the daemon's `Notifier` (`daemon/src/prismis_daemon/notifier.py`), which shells out to `terminal-notifier` on the machine the daemon runs on. That only reaches a person when the daemon runs on the Mac they sit at. rudy's daemon runs on <host>, a Linux server, and the observability log there records every HIGH batch from 2026-10-16 to 2026-10-22 as `notification.send` with status `error` or `skipped`/`command_not_found`: 57 batches, none delivered. rudy had not noticed, because even where it works the alert is thin: one banner ("N new high priority items", title cut to 50 characters), no click action, nothing to look back at.

The question was whether to drop notifications or replace them with something worth having.

## Journey

- **Drop it, or do better.** Since rudy never missed the notifier, removing it was the first option on the table. rudy wanted better on the Mac instead, pointing at the menu-bar icon earshot's app puts in the top right.
- **Earshot as the frame.** `~/development/projects/earshot/app` is a SwiftPM package of about 730 lines: a SwiftUI `MenuBarExtra`, native notifications through `UNUserNotificationCenter`, a login item through `SMAppService`, and a testable `EarshotCore` target with no UI kept apart from the app target. That shape fits a prismis menu-bar app directly.
- **Configurable endpoint.** rudy required the daemon URL to be configurable, since some users run the daemon locally and he runs it on <host>. This turned out to exist already: `~/.config/prismis/config.toml` carries `[remote] url` and `key`, which the TUI reads through `HasRemoteConfig`/`GetRemoteURL` in `tui/internal/config/config.go` (the 2025 remote-TUI design, `docs/explorations/2025-11-05-remote-tui-design.md`). A local install has no `[remote]` but serves the same API on its `[api]` host and key. Reusing that rule means no new setting, and the TUI and the app can never point at different daemons.
- **What it shows.** Both an alert when something HIGH lands and a list in the dropdown. `GET /api/entries` already filters by `priority`, `unread_only`, `since` and `changed_since`, so the list needs no daemon change.
- **Where a click lands.** Considered: the original article (free, but skips prismis's summary), the TUI reader (a menu-bar app would have to launch a terminal), and the web view's detail panel. The panel won: it holds the reading summary, deep synthesis and quotes, and the daemon serves it wherever it runs. The web view has no way to open straight to one item today, so it needs a deep link.
- **Read status.** The web view never marks an item read on open; it has an explicit Mark Read. The app follows the same rule rather than inventing its own, and may offer a mark-read action in the dropdown.
- **Distribution.** A ready-built app installed as a cask needs Apple notarization to open cleanly on other people's Macs, which needs a paid Developer ID ($99 a year for an individual). rudy declined the fee. The app builds and signs on the user's own Mac the way earshot's `scripts/build-app.sh` does, with a locally created signing identity so macOS keeps the app's permissions across rebuilds. A locally built app is never quarantined, so it opens without warnings; the cost is that installing needs Xcode's command-line tools and a build step. The prismis tap (`nickpending/prismis`, three formulas) is the only brew tap rudy publishes; the app does not join it as a cask.
- **The old notifier.** Kept as a fallback was rejected: it only worked with the daemon on the same Mac, and two places deciding what counts as a new HIGH item would drift.

## Decisions

- A Mac menu-bar app replaces the daemon's notifier for alerting on new HIGH items.
- The app finds its daemon the way the TUI does: `[remote] url`/`key` in `~/.config/prismis/config.toml` when present, otherwise the local daemon's `[api]` host and key from the same file.
- It shows a count of unread HIGH items, lists them in its dropdown, and sends a native notification for each it has not alerted on before.
- It reads items through the existing `GET /api/entries` filters; the daemon gains no endpoint for it.
- Clicking an item opens that item's detail panel in the daemon's web view, which gains a link that opens straight to one item.
- Opening an item does not mark it read; marking read stays an explicit action, as in the web view.
- The app remembers on disk which items it has already alerted on, so a restart or a re-scored item never alerts twice.
- It checks the daemon every few minutes.
- It lives in `macos/` in the prismis repository, split as earshot is into a testable core with no UI and the menu-bar app.
- It builds and self-signs on the user's Mac, as earshot does; no Developer ID, no notarized cask.
- The daemon's `terminal-notifier` notifier is removed once the app ships, not kept as a fallback.

## Candidate features

- **Mac users cannot see or be alerted to new HIGH items when the daemon runs elsewhere.** A user whose daemon runs on another machine gets no alert at all when a HIGH item lands, and a user whose daemon runs locally gets one unclickable banner with nothing to look back at. Neither has a way to glance at what is new and HIGH and open it.
- **The daemon carries a notifier that can only reach someone sitting at the daemon's own machine.** On a server it records a skipped or failed notification for every HIGH batch and alerts no one, and once a Mac-side alert exists it is a second place deciding what counts as new.

## Deferred

- A notarized, ready-built cask: waits on a Developer ID rudy chose not to buy.
- Alerts on platforms other than macOS.
- Alerts for priorities below HIGH, and any per-source or per-topic alert rules.
