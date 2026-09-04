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

Write that symbol reference as prose, not as a call signature. `ERA001` is enabled and reads
`symbol_name (path/to/file.py)` inside a comment as commented-out code, which turns the gate red —
phrase it as "the `symbol_name` helper in `path/to/file.py`" instead. The citation rule and the
dead-code rule pull against each other in exactly this spot.

A test found defective is not fixed until the replacement has been shown to fail. Disable the
behavior under test — delete the timeout, the filter, the branch it covers — confirm the test goes
red, then restore it; a replacement that still passes with the logic removed proves no more than
the tautology it replaced. Two shapes recur and both survive review. A body with no assertion at
all looks like an honest exercise and is invisible whenever the callee swallows its own exceptions,
so check what the called code does with a failure before trusting that reaching the end of the test
means anything. An assertion on a wrapper message passes for every failure path that shares the
wrapper, so it cannot tell the failure under test from an unrelated one; assert on the
distinguishing outcome instead — the exception type or its chained cause, the recorded side effect,
the value only the correct path produces. Then treat the finding as a class rather than an
instance: before closing it, grep every test directory for the same shape and report what the sweep
found, because the file that held one live third-party URL or one always-true assertion holds
others.

Never describe a one-time manual check as if it were a standing guard. "Proved by planting a file
and observing the result" reads as a regression test to everyone downstream; if no test asserts it,
say the check was manual and unguarded, or write the test. A work order that records proofs the
tree does not contain is worse than one that records nothing.
