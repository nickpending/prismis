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

Never write a line number into a comment, docstring, test header, or work-order table from memory
or from a read earlier in the session. Open the file and read the target line as you write the
citation, and prefer naming the symbol over a bare line range — a symbol survives edits above it,
a line number does not. The same rule binds counts: when a table reports a total and a breakdown,
re-derive every row from the tree in the pass that writes the total, and require the rows to sum
to it. A citation or count that is wrong on arrival sends the next reader to unrelated code and
discredits the surrounding claims, which are usually correct.
