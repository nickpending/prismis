---
type: design
date: 2026-10-10
title: "Design: homebrew-formulas"
description: "A Mac user installs exactly the prismis component they need with one brew command, the TUI for a person reading, the CLI for agents, the daemon for the machine that fetches, each at the versions the gate tested, without cloning the repo or installing Go and uv by hand."
purpose: "The recorded design of the homebrew-formulas work order, read by its plan and its builders."
producer: cli:shape
---

# Design: homebrew-formulas

## Purpose

A Mac user installs exactly the prismis component they need with one brew command, the TUI for a person reading, the CLI for agents, the daemon for the machine that fetches, each at the versions the gate tested, without cloning the repo or installing Go and uv by hand.

## Form

The formulas are written and tested in this repository and published to the public nickpending/homebrew-prismis tap, Homebrew's standard layout (its tap resolution maps `brew install nickpending/prismis/<formula>` to that repo).

1. Source of truth here: packaging/homebrew/ holds prismis-tui.rb, prismis-cli.rb and prismis-daemon.rb, plus a render script (packaging/homebrew/render.py) that, given a release tag, fills each formula's url (the tag's GitHub source tarball), sha256 and version, and generates prismis-cli's `resource` blocks from cli/uv.lock's sdist url and hash for every package the client-only install needs (no [local] extra), so resources are lock-pinned and never hand-maintained. Each formula also carries a `head` spec on main, which is how the lane and CI install them before a release exists.

2. prismis-tui: depends_on go at build; builds tui/ with std_go_args into bin/prismis (the name make install-tui uses), natively with cgo for mattn/go-sqlite3; no prebuilt binaries. Its test runs the binary's help and exits 0.

3. prismis-cli: Language::Python::Virtualenv on python@3.14; installs cli/ client-only with the rendered resources. cli/pyproject.toml gains the explicit [build-system] (hatchling) daemon/pyproject.toml already declares, so brew's pip path can build it. Its caveats print the one setup step (the [remote] url and key in ~/.config/prismis/config.toml) and the check `prismis-cli list --limit 1`. Its test runs `prismis-cli --help`.

4. prismis-daemon: depends_on python@3.14 and uv; creates a virtualenv in libexec and installs daemon/ with uv using `uv export --frozen --no-dev --no-emit-project` of the tarball's daemon/uv.lock as constraints (binary wheels; torch, tokenizers and pydantic-core cannot build under brew's --no-binary path), the same pinning make install-daemon uses; links bin/prismis-daemon. A `service` block runs it under launchd with keep_alive and logs under var/log; keys come from ~/.config/prismis/.env, which the daemon already loads. Caveats print the first-run path: OPENROUTER_API_KEY in ~/.config/prismis/.env, `prismis-cli context bootstrap`, `prismis-daemon verify`, `brew services start prismis-daemon`. Its test runs `prismis-daemon --help`.

5. Publishing is a release step, outward-facing and run with the operator's go: tag a release, render the three formulas for the tag, push them to homebrew-prismis. The README Installation section leads with the three brew commands and keeps make install for development.

## Commitments

- Each of prismis-tui, prismis-cli and prismis-daemon installs alone with `brew install --HEAD` from a local tap built from this tree, and its `brew test` passes.
- prismis-cli's resources are generated from cli/uv.lock, and a test fails if the committed formula's resources differ from what the lock renders.
- prismis-daemon installs the exact daemon/uv.lock versions: after install, every locked package in its libexec environment has its locked version.
- prismis-daemon's service block produces a launchd plist that runs prismis-daemon, and the daemon started that way reads keys from ~/.config/prismis/.env.
- The render script, given a tag and its tarball, writes url, sha256 and version into all three formulas and nothing else changes.
- Each formula's caveats name its setup step, and the README leads with the brew commands.

## Sacrifices

- prismis-daemon downloads its wheels from PyPI at install time instead of shipping a bottle, so an install needs network access and takes as long as the torch download.
- prismis-tui builds from source on each user's Mac, so installing it pulls Go as a build dependency.
- Publishing to the tap is a release step run with the operator's go, not automatic on every merge.
- Linux hosts such as cerebro keep make install; the formulas target macOS.

## Risk

brew's Python tooling changes between Homebrew releases; the render script and the `brew test` runs are the check, and a formula that stops installing fails at the next release's install check rather than for users first.

## Licensed by

Makefile:1
