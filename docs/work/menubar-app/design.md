---
type: design
date: 2026-10-27
title: "Design: menubar-app"
description: "Give a Mac user a glanceable, clickable view of what is new and HIGH in their prismis, wherever their daemon runs: a menu-bar app that counts and lists unread HIGH items, notifies once per new one, and opens a clicked item in the web view's detail panel, so HIGH items stop going nowhere when the daemon is on another machine."
purpose: "The recorded design of the menubar-app work order, read by its plan and its builders."
producer: cli:shape
---

# Design: menubar-app

## Purpose

Give a Mac user a glanceable, clickable view of what is new and HIGH in their prismis, wherever their daemon runs: a menu-bar app that counts and lists unread HIGH items, notifies once per new one, and opens a clicked item in the web view's detail panel, so HIGH items stop going nowhere when the daemon is on another machine.

## Form

Two jobs, the web link first.

1. The link (job 1, this work order). The web view (daemon/src/prismis_daemon/static/index.html) opens one item when its URL carries `#item=<id>`. The id is read from the hash, never the query, so it is never sent to the server or written to its access log. On start-up, once an API key is held (at init, or right after the key modal saves one), and on every later hashchange, a hash naming an item opens that item through the existing openDetail. The id must be a canonical lowercase UUID, the form storage gives every item (`str(uuid.uuid4())`); anything else is ignored and fetches nothing, since the id is interpolated into openDetail's request path. Closing the panel removes the hash without adding a history entry, so a reload does not reopen it. API.md documents the link.

2. The app (job 2, its own work order). A SwiftPM package in macos/ split as earshot's app is: PrismisCore, with no UI, holds the config reader, the API client, the alerted-items store and the poll decision; the Prismis target holds the SwiftUI MenuBarExtra, the notifier over UNUserNotificationCenter and the login toggle over SMAppService.
   - Config: it reads ~/.config/prismis/config.toml, tracking sections, since `key` appears under both [api] and [remote]. With a [remote] url it uses that url and [remote] key; otherwise http://127.0.0.1:8989 (the daemon's fixed port) with [api] key. Like earshot it refuses a file any group or other bit is set on, and refuses plain http to anything but loopback, since the key would travel in the clear.
   - Poll: every five minutes, and on demand from the menu, GET /api/entries?priority=high&unread_only=true&view=list&sort_by=date with the key in X-API-Key. The icon shows the count; the dropdown lists the items newest first, title and source, each opening <daemon url>/#item=<id> in the default browser. A failed poll shows the daemon as unreachable with the reason, keeps the last list, and logs through os.Logger; it never alerts.
   - Alerts: one native notification per listed item not in the alerted store, its title and source; clicking it opens the same link. The store is a JSON file of item id and first-alerted time under ~/Library/Application Support/Prismis/, written atomically; entries older than 30 days are dropped. The first poll with no store seeds it with every listed item and alerts none, so installing does not flood.
   - Install: macos/scripts/build-app.sh builds release and self-signs Prismis.app with a local identity in its own keychain, as earshot's does; `make install-menubar` runs it and copies the app to ~/Applications. Launch at login is a menu toggle, never set by installing.
   - Gate: .specify/verify.sh discovers macos/Package.swift and runs swift build, swift format lint and swift test on Darwin; on Linux it lists the unit as uncovered rather than passing it. CI adds a macos-latest job running verify.sh, so every unit is covered by the gate CI runs.

## Commitments

- The web view opens the detail panel of the item a `#item=<id>` link names, at load and on hash change, once an API key is held.
- An item id that is not a canonical lowercase UUID opens nothing and makes no request.
- Closing the panel opened from a link removes the hash without a new history entry.
- The app reaches the daemon named by [remote] url and key when present, otherwise the local daemon at http://127.0.0.1:8989 with [api] key, and refuses a config readable by others or plain http off loopback.
- The app shows the count and list of unread HIGH items from GET /api/entries, and each listed item opens its web detail panel.
- The app sends exactly one notification per HIGH item across polls and restarts, and none on its first poll.
- A failed poll shows the daemon as unreachable, keeps the last list, and sends no notification.
- `make install-menubar` builds, self-signs and installs the app; installing never enables launch at login.
- CI runs the Swift unit on macOS through .specify/verify.sh, and verify.sh on Linux reports the unit uncovered instead of passing it.

## Sacrifices

- Installing needs Xcode's command-line tools and a local build; there is no ready-built, notarized app.
- The app is macOS only.
- Marking read is not offered in the menu; it stays in the web view and TUI.
- An item re-scored to HIGH more than 30 days after its first alert can alert again.
- A HIGH item that arrives and is read within one five-minute poll never alerts.
- The daemon's fixed local port 8989 is repeated in the app, as the CLI and TUI already repeat it; changing the daemon's port means changing all three.

## Risk

If the link's id check is loosened, a crafted link could steer openDetail's authenticated fetch to another GET path on the daemon; the UUID check is the guard and its test is the proof. If a user's [remote] url points at a proxy that answers the web view at a different path than the API, the link opens the wrong page; the link is built from the same base url the TUI already uses, so the two fail together and visibly.

## Licensed by

daemon/src/prismis_daemon/static/index.html:2052
