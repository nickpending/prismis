---
type: design
date: 2026-10-27
title: "Design: menubar-mac"
description: "Give a Mac user a glanceable, clickable view of what is new and HIGH in their prismis wherever their daemon runs, on top of job 1's live #item link: a menu-bar app that counts and lists unread HIGH items, notifies once per new one, and opens an item in the web view's detail panel."
purpose: "The recorded design of the menubar-mac work order, read by its plan and its builders."
producer: cli:shape
---

# Design: menubar-mac

## Purpose

Give a Mac user a glanceable, clickable view of what is new and HIGH in their prismis wherever their daemon runs, on top of job 1's live #item link: a menu-bar app that counts and lists unread HIGH items, notifies once per new one, and opens an item in the web view's detail panel.

## Form

The menu-bar app, transcribed from the accepted menubar-app design (docs/work/menubar-app/design.md, item 2); job 1's #item link is live (2e8ea9b).

A SwiftPM package in macos/ (platforms macOS 14) split as earshot's app is: PrismisCore, with no UI, holds the config reader, the API client, the alerted-items store and the poll decision; the Prismis target holds the SwiftUI MenuBarExtra, the notifier over UNUserNotificationCenter and the login toggle over SMAppService.
   - Config: it reads ~/.config/prismis/config.toml, tracking sections, since `key` appears under both [api] and [remote]. With a [remote] url it uses that url and [remote] key; otherwise http://127.0.0.1:8989 (the daemon's fixed port) with [api] key. Like earshot it refuses a file any group or other bit is set on, and refuses plain http to anything but loopback, since the key would travel in the clear.
   - Poll: every five minutes, and on demand from the menu, GET /api/entries?priority=high&unread_only=true&view=list&sort_by=date with the key in X-API-Key. The icon shows the count; the dropdown lists the items newest first, title and source, each opening <daemon url>/#item=<id> in the default browser. A failed poll shows the daemon as unreachable with the reason, keeps the last list, and logs through os.Logger; it never alerts.
   - Alerts: one native notification per listed item not in the alerted store, its title and source; clicking it opens the same link. The store is a JSON file of item id and first-alerted time under ~/Library/Application Support/Prismis/, written atomically; entries older than 30 days are dropped. The first poll with no store seeds it with every listed item and alerts none, so installing does not flood.
   - Install: macos/scripts/build-app.sh builds release and self-signs Prismis.app with a local identity in its own keychain, as earshot's does; `make install-menubar` runs it and copies the app to ~/Applications. Launch at login is a menu toggle, never set by installing.
   - Gate: .specify/verify.sh discovers macos/Package.swift and runs swift build, swift format lint and swift test on Darwin; on Linux it lists the unit as uncovered rather than passing it. CI adds a macos-latest job running verify.sh, so every unit is covered by the gate CI runs.
   - Notification click: the app is its notification centre's delegate, and a click on an item's notification opens that item's link, as a click in the dropdown does.
   - verify.sh's Swift unit runs swift format lint --strict, swift build and swift test in macos/, passing the same Command Line Tools flags for Testing.framework and its macro plugin that ember's gate passes when the developer dir is Command Line Tools, so both gates agree on this Mac.

## Commitments

- The app reaches the daemon named by [remote] url and key when present, otherwise the local daemon at http://127.0.0.1:8989 with [api] key, and refuses a config readable by others or plain http off loopback.
- The app shows the count and list of unread HIGH items from GET /api/entries, and each listed item opens its web detail panel.
- The app sends exactly one notification per HIGH item across polls and restarts, and none on its first poll.
- A failed poll shows the daemon as unreachable, keeps the last list, and sends no notification.
- `make install-menubar` builds, self-signs and installs the app; installing never enables launch at login.
- CI runs the Swift unit on macOS through .specify/verify.sh, and verify.sh on Linux reports the unit uncovered instead of passing it.
- Clicking an item's notification opens its web detail panel, as clicking it in the dropdown does.

## Sacrifices

- Installing needs Xcode's command-line tools and a local build; there is no ready-built, notarized app.
- The app is macOS only.
- Marking read is not offered in the menu; it stays in the web view and TUI.
- An item re-scored to HIGH more than 30 days after its first alert can alert again.
- A HIGH item that arrives and is read within one five-minute poll never alerts.
- The daemon's fixed local port 8989 is repeated in the app, as the CLI and TUI already repeat it; changing the daemon's port means changing all three.
- verify.sh repeats the Command Line Tools flags ember's gate computes for swift test, so a change to where Command Line Tools keeps Testing must reach both.

## Risk

If the poll or the alerted store misbehaves, the user is either flooded or silently told nothing, the two ways a notifier fails; the first-poll seed and the failed-poll state are the guards, each with a test against a real local HTTP server. If the self-signed identity is lost, a rebuild changes the app's signature and macOS asks for notification permission again, which the user can grant; nothing else depends on it.

## Licensed by

docs/work/menubar-app/design.md:22
