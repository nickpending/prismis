# prismis

Local-first content pipeline: a Python daemon (`daemon/`), a Go TUI (`tui/`), a Python CLI
(`cli/`). Vision in `docs/IDEA.md`; code-law in `.specify/memory/constitution.md`.

Verification is `./.specify/verify.sh` — it discovers its own units, runs every check, collects
every failure, and reports what it covered. CI runs that same script.

## Learned Patterns

pyright covers only production sources in this repo: `daemon/pyproject.toml` sets
`include = ["src/prismis_daemon"]` and `cli/pyproject.toml` sets `include = ["src/cli"]`. Test
files in `daemon/tests` and `cli/tests` are never typechecked. A green gate line reading
`pyright(daemon)` or `pyright(cli)` says nothing about test code — do not cite it as evidence that
a change to tests is type-correct. When a change is mostly or entirely in tests, the typecheck
steps prove nothing about it, and the claim of coverage must be scoped to `src` accordingly.
