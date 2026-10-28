---
type: design
date: 2026-10-28
title: "Design: remove-notifier"
description: "Take the desktop notifier out of the daemon now that the macOS menu-bar app carries the HIGH signal: it reached no one once the daemon ran on a server, and its test fails on the macOS CI runner."
purpose: "The recorded design of the remove-notifier work order, read by its plan and its builders."
producer: cli:shape
---

# Design: remove-notifier

## Purpose

Take the desktop notifier out of the daemon now that the macOS menu-bar app carries the HIGH signal: it reached no one once the daemon ran on a server, and its test fails on the macOS CI runner.

## Form

1. Delete daemon/src/prismis_daemon/notifier.py and its tests (test_notifier_unit, test_notifier_observability_unit, test_notifier_integration).
2. The orchestrator takes no notifier: DaemonOrchestrator's notifier parameter and attribute go, run_once no longer tracks new_high_priority_items in its stats or calls notify at the cycle's end, and every caller (__main__, verify_chain, tests) stops passing one.
3. Config drops high_priority_only and notification_command. A config.toml that still has a [notifications] section loads as before and logs one warning, in the shape of the retired [context] keys warning beside it: the section is ignored because the desktop notifier was removed, and the macOS menu-bar app (make install-menubar) carries new HIGH items.
4. The config template (DEFAULT_CONFIG_TOML) no longer writes [notifications].
5. The verify chain drops its notify link and _notify_link; --full now means only "also deep-extract", and its help says so.
6. docs/configuration.md drops the [notifications] section and names the menu-bar app; docs/architecture's components, boundaries, decisions and manifest drop or update the notifier where they name it; README and docs/IDEA.md lines naming desktop notifications point to the menu-bar app.

## Commitments

- No daemon code path sends a desktop notification or runs a notification command.
- A config.toml with a [notifications] section loads, keeps every other setting, and logs one warning naming the section and its replacement; one without it logs nothing about it.
- A new install's config template has no [notifications] section.
- The verify chain reports no notify link, and --full still runs and reports deep extraction.
- No document describes the daemon notifier as a current feature.

## Sacrifices

- A daemon on a Mac without the menu-bar app no longer shows a desktop banner for new HIGH items.

## Risk

If a caller still passes notifier= after the parameter goes, it fails at startup; the full daemon and verify-chain tests construct the orchestrator through the real entry points, so a missed caller fails the gate.

## Licensed by

docs/explorations/menubar-app.md:41
