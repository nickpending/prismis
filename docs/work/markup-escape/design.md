---
type: design
date: 2026-10-18
title: "Design: markup-escape"
description: "Make every piece of external text the daemon and CLI print inside Rich markup survive intact, so a config error's '[api]' hint, a post title's '[Draft]' prefix or an API message reaches the user as written, and make that hold by a guard the test suite runs, not by review."
purpose: "The recorded design of the markup-escape work order, read by its plan and its builders."
producer: cli:shape
---

# Design: markup-escape

## Purpose

Make every piece of external text the daemon and CLI print inside Rich markup survive intact, so a config error's '[api]' hint, a post title's '[Draft]' prefix or an API message reaches the user as written, and make that hold by a guard the test suite runs, not by review.

## Form

1. The rule. Inside an f-string that carries a Rich markup tag and is passed to a print, every interpolated expression is wrapped in rich.markup.escape, as escape(e) for a string or escape(str(x)) for anything else. The template's own tags stay literal; only values are escaped. No new helper: rich.markup.escape is already the primitive at three sites (daemon __main__.py migrate_config, cli report.py, cli get.py).

2. The sweep. All interpolations inside markup prints across daemon/src and cli/src are wrapped: 211 today across 15 files by this session's AST count (41 of them the exception sites the issue's grep found; the rest titles, service names, paths, reasons, counts and times). Counts and times go through escape(str(...)) so one rule covers every site with no allowlist.

3. The guard. A test in daemon/tests/unit on the test_no_internal_mocks_unit.py pattern parses every .py under daemon/src and cli/src, finds each call to a .print attribute, and for each f-string argument whose literal parts contain a markup tag ([name], [/name], [#hex], [bold red] and the like) checks that every FormattedValue is a call to escape; a bare interpolation is a Violation naming file, line and the expression. The test asserts no violations, printing them as the failure message, and a planted-defect test writes a file with one unescaped interpolation into a temp tree and asserts the scan reports exactly it, so the guard is shown to fire.

4. The fatal-path proof. A test builds a config.toml without an [api] section, runs the daemon's real startup path that prints 'Fatal error: {e}' through the real console into a capture, and asserts the text 'Add [api] section' appears; it is red against today's code. A sibling does the same for the outdated-[llm] error through verify's 'config: {e}' line.

5. Out of this work: log lines (logger.*) are not Rich markup and are untouched; the TUI is Go and renders no Rich markup.

## Commitments

- Every interpolation inside a Rich markup string printed by the daemon or CLI is escaped, so external text renders as written, brackets included.
- A new unescaped interpolation inside a markup print fails the daemon test suite, naming the file, line and expression.
- The daemon's fatal path prints the missing-[api] config error with '[api]' intact, proven through the real console.
- No print loses its colour tags: escaping applies to values, never to the template.

## Sacrifices

- Every markup print that interpolates a value grows an escape() call, 211 sites, which is churn with no behaviour change at the sites whose values never carried brackets.
- Rich's escape leaves text that merely looks like a tag but is not one unchanged; a value containing a literal backslash before a bracket is rendered by Rich's own rules.
- The guard reads f-strings only; a markup string built by concatenation or .format and then printed is outside its sight, as it is outside the pattern the codebase uses.

## Risk

If a future print needs an interpolated value to carry live markup on purpose (a value that is itself a style tag), the guard forbids it as written; that site would build its string outside the f-string, and the guard's sacrifice names that path. If escape(str(x)) is forgotten on a non-string, escape raises at that print; the guard catches the missing wrapper before that.

## Licensed by

daemon/src/prismis_daemon/__main__.py:607
