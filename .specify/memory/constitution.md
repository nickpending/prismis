<!--
Sync Impact Report
==================
Version change: (unset template) → 0.1.0
Bump rationale: Initial ratification. Adopts the bench house-default principle set (v0.4.0)
                in full; no prior version existed to compare against.

Modified principles: none (initial adoption)
Added sections:
  - Core Principles I-VIII (house-default, verbatim names and wording)
  - Security Requirements (house-default)
  - Quality Gates (house-default)
  - Project Constraints (prismis-specific, derived from docs/IDEA.md)
  - Governance (house-default)
Removed sections: none

Project-authored principles: none yet. When prismis adds its own, they are numbered IX
onward, after the house set, and are never renumbered by a house sync.

Follow-up TODOs: none. RATIFICATION_DATE is today — this is the initial adoption, so the
original adoption date is known and requires no deferral.
-->

# prismis Constitution

## Core Principles

### I. Tests Prove the Logic (NON-NEGOTIABLE)

Every module either has tests exercising its logic directly, or the plan records it as glue. The plan
carries one row per module it creates or materially changes, in exactly this shape:

| Module (path) | logic \| glue | Test that proves it, or why glue |

Glue is a claim that the module holds nothing that can be wrong independent of its types — the third
column states what it holds instead. A module absent from that table is a Constitution Check failure,
not an omission. The classification is written before the implementation: a module classified after
the code exists is classified by whoever does not want to write the test.

Tests exercise **real** collaborators — your own code, and the real DB / cache / services you own and
can run. Mocking is reserved for the **true external boundary**: third-party or paid APIs you don't
control (LLM providers, payment, email). Internal code is never mocked, and no fake stands in for infra
you could run for real — a test against a mock proves the mock, not the system.

"Done" means executed and observed, never read.

### II. Failures Are Distinguishable (NON-NEGOTIABLE)

Success, empty success, and failure are three answers and never share a representation. A missing
dependency, a refused request, and a genuine zero result must be tellable apart by the caller without
reading logs. Never acknowledge an action before the thing that can refuse it has run.

### III. Behavior Leaves a Record

Anything running unattended — scheduled, triggered, retried, backgrounded — records what it decided
and why, at the moment it decides. A system whose behavior can only be reconstructed by reasoning
about its code cannot be debugged once the reasoning runs out.

### IV. Verified Where It Runs

Verification executes in the environment the code runs in — the daemon's shell, CI's container, the
sealed runtime — not the developer's interactive session. Anything the interactive shell supplies (a
PATH entry, a version manager, a credential, a TTY) is absent there and must not be depended on.

### V. Dependencies Point One Way

A module depends only on layers beneath it. Libraries never import their consumers' frameworks; core
logic never imports transport, CLI, or UI. An upward dependency couples every consumer to that
framework, and the break surfaces in an unrelated package.

### VI. Destructive Operations Are Recoverable

Anything that overwrites, deletes, or migrates reads the target first and leaves a way back — a
backup, a dry run, a reversible step. Irreversible operations are explicit, never a side effect of a
convenience.

### VII. Simplicity & YAGNI

Use the framework and existing primitives before building. New abstraction, scaffolding, or a parallel
mechanism must be justified against the simpler alternative it displaces (recorded in Complexity
Tracking). Machinery that duplicates the framework rots against its updates — build it only when the
simpler path is proven insufficient.

### VIII. Additive Change & Versioning

Interfaces, schemas, and contracts change additively — add fields and endpoints, deprecate with a
path. A breaking change requires an explicit migration and a version bump, never a silent in-place
removal that callers depend on.

Persisted data is a contract with the past: a change to a stored shape ships with the migration or
backfill that makes existing records conform, in the same change that introduces it.

## Security Requirements

Validate input where it enters; parameterize every query; secrets live in env/config, never inline —
no exception for test or throwaway code. Secrets never leave the process: not in logs, error messages,
or responses. Credentials at rest are hashed, never recoverable.

## Quality Gates

A feature's completion is gated on **executing** its declared verification — typecheck + tests +
quickstart's runnable steps — exiting clean. The declaration cannot be empty: a gate that passes
having executed nothing fails this constitution whatever its exit code. Reading artifacts without
running them does not satisfy this gate; a verification that cannot be executed trapdoors to a human.
Correctness of executable code is proven by execution, not review.

## Project Constraints

These are prismis-specific facts that the principles above apply against. They are constraints, not
additional principles — they do not carry principle numbering and a house sync never touches them.

**Local-first, zero-ops.** prismis runs entirely on the operator's own machines against SQLite. There
is no server tier to fall back on and no ops team to page. A failure that requires manual intervention
to recover from has already failed, which is what makes Principle VI (recoverable destructive
operations) and Principle III (unattended behavior leaves a record) load-bearing rather than
aspirational here.

**Three components, one contract.** The Python daemon (`daemon/`), the Go TUI (`tui/`), and the Python
CLI (`cli/`) are separate units that meet at the daemon's HTTP API and the SQLite schema. Under
Principle V, the daemon never imports from the TUI or CLI, and under Principle VIII, that API and
schema change additively — the TUI and CLI are deployed independently and will be running an older
build when the daemon changes.

**The pipeline runs unattended.** Fetching, summarization, evaluation and deep extraction execute on a
schedule with nobody watching. Under Principle II, a content item that failed to summarize and one
that legitimately produced nothing must be distinguishable in storage — degraded output that still
looks like output is the characteristic failure of this system, and it is invisible unless the
distinction is represented.

**LLM providers are the true external boundary.** They are the one collaborator Principle I permits
mocking, and the only one. The database, the HTTP API, and every internal module are exercised for
real.

## Governance

This constitution supersedes other practices for what it covers. Amendments require a rationale, and a
breaking change to the rules requires a migration note. Complexity that violates a principle must be
justified in Complexity Tracking, or the plan fails its Constitution Check.

Principles I-VIII come from the bench house-default and are non-negotiable. A project adds its own
principles after them, under its own numbering; it never deletes or rewords a house principle. The
**House-default** field below records the house version this project is synced to — when the house
default moves ahead, `/speckit-constitution` carries the delta in, and its consistency propagation
updates the plan, spec, and tasks templates to match.

**Version**: 0.1.0 | **House-default**: 0.4.0 | **Ratified**: 2026-09-03 | **Last Amended**: 2026-09-03
