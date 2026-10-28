# remove-notifier — post-merge walk

**C2, run 2026-10-28 after deploying fd294a2 to the operator's remote host**, whose config.toml still has a `[notifications]` section.

- The daemon restarted at 01:43:35 UTC and is active. Its journal holds exactly one line about the section: "config.toml [notifications] section is ignored: the daemon's desktop notifier was removed, and the macOS menu-bar app (make install-menubar) shows new HIGH items. Delete the section."
- The cycle that started at 01:43:36 completed at 01:49:20 with no `notification.send` event in the observability log.

Checked by reading the host's journal and observability log once; no standing test asserts the live deployment.
