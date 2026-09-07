# Plan: wo-reddit-validation-seam

Work order: `/Users/rudy/development/projects/prismis/docs/work/wo-reddit-validation-seam.md`

## Complexity

5 — design locked, but five files across `src` and both test trees, and the hardest part is
writing tests that satisfy a constitution forbidding the obvious shortcut.

## Constitution Check (Principle I — NON-NEGOTIABLE)

One row per module created or materially changed. Written before implementation.

| Module (path) | logic \| glue | Test that proves it, or why glue |
|---|---|---|
| `daemon/src/prismis_daemon/validator.py` | **logic** | `tests/unit/test_reddit_validation_unit.py` (new) — parse over 5 URL forms, interpret over 8 exception outcomes, the `env:`-placeholder and `None`-config guards. All network-free. |
| `daemon/src/prismis_daemon/api.py` (`get_validator`) | **logic** | `tests/integration/test_api_integration.py` — SC-11 (as corrected) asserts `get_validator` returns a validator holding `config=None` rather than propagating the raise, and that the API still refuses authenticated requests inside the envelope. The config-failure branch is a decision, not a pass-through, so it is logic. |
| `daemon/src/prismis_daemon/api.py` (`add_source` / `update_source` signatures) | **glue** | Holds no independent decision — parameter wiring to `Depends(get_validator)`. Its behaviour is covered transitively by SC-7 and SC-11; a bug here is a type error pyright catches. |

## Approach

### 1. Split `_validate_reddit` into three (SC-6)

- **`_parse_subreddit(url) -> str | None`** — pure. Keeps every form the current code handles:
  `reddit://NAME`, `reddit.com/r/NAME`, `old.reddit.com/r/NAME`, bare name, unparseable → `None`.
  The `reddit.com` substrings stay; they are the parser, not the bug.
- **`_probe_subreddit(name) -> object`** — the only network. Builds the client, forces the fetch,
  returns the subreddit or raises.
- **`_interpret_reddit_outcome(exc_or_result) -> tuple[bool, str | None, dict | None]`** — pure.
  Maps outcome → the existing 3-tuple.

`_validate_reddit` becomes the composition. **Keep the method name** — `test_validator_integration.py:137`
calls it directly and `verify.sh` never runs that file, so a rename breaks it invisibly (SC-14).

### 2. Credential gate before any network (SC-10, D-ENVGUARD)

Before constructing PRAW, reject unusable credentials:

```
config is None                      -> "Reddit credentials not configured"
client_id/secret empty              -> same
client_id/secret startswith "env:"  -> same
```

The `env:` case is not cosmetic. `expand_env_var` is `os.environ.get(env_var, value)`, so an unset
variable yields the literal truthy string `"env:REDDIT_CLIENT_ID"`. Without this guard PRAW gets a
garbage id, Reddit returns 401, and every unconfigured install is told its **credentials are
invalid** rather than absent — collapsing exactly the two answers Principle II requires be
distinct. `Config.validate()` already tests this shape; reuse it, do not invent a second one.

### 3. Outcome mapping (SC-2)

Route every prawcore exception explicitly. The outer `except Exception` in `validate_source` is a
catch-all that `daemon/pyproject.toml`'s repo-wide `BLE001` suppression hides, so anything left
unrouted silently collapses and no lint will say so.

| outcome | message names |
|---|---|
| success | valid; metadata `display_name` from `display_name_prefixed` (SC-13) |
| `NotFound`, `Redirect` | subreddit does not exist |
| `Forbidden` | private or quarantined |
| `UnavailableForLegalReasons` | unavailable for legal reasons |
| `TooManyRequests` | rate limited, retry later |
| `ResponseException` w/ `status_code == 401` | **credentials invalid or expired** |
| `RequestException` | network failure |
| any other `PrawcoreException` | its own explicit message — never the generic catch-all |

Messages carry **no** credential substring (SC-2, constitution Security Requirements): prawcore
exception text reaches the 422 body via `ValidationError(f"Source validation failed: {error_msg}")`.

### 4. Bounding the probe (SC-12, D-TIMEOUT)

`prawcore.const.TIMEOUT` is computed **at import** from `PRAWCORE_TIMEOUT`, and
`prawcore/sessions.py` binds it as a default argument value at function-definition time. So neither
setting the env var at runtime nor patching `prawcore.const.TIMEOUT` reaches it. Two things that
do work, and we use both:

- `check_for_updates=False` on the `praw.Reddit(...)` call — `praw.Reddit.__init__` calls
  `_check_for_update()`, which reaches `pypi.org` and unpickles a temp file. Verified by
  execution. `fetchers/reddit.py` does not pass it either; out of scope here, filed as **gh #67**.
  (Correction: that pypi call is gated on a class attribute, so it is once per process, not once
  per fetch cycle.)
- A custom `requests.Session` passed via `requestor_kwargs`, whose `request()` clamps `timeout`
  to the validator's budget. prawcore passes `timeout=` explicitly on every call, so the clamp
  lands.

Retries still allow up to 3 attempts, so the clamp bounds each attempt, not the total. The **total**
is bounded at the call site: `add_source` and `update_source` are `async def`, and
`validate_source` is blocking, so they `await asyncio.wait_for(asyncio.to_thread(...), timeout)`.
That is what keeps one slow subreddit from stalling every concurrent request.

**Honest limitation to record, not paper over:** `wait_for` stops the *waiting*; it cannot kill the
worker thread. A pathological probe can leave one thread running to prawcore's own budget. Bounded
and rare, and strictly better than today's unbounded blocking call.

### 5. The seam (SC-5, SC-11, D-CONFIG)

```
async def get_validator(...) -> SourceValidator:
    try:    config = Config.from_file()
    except Exception: config = None      # degrade only the path that needs config
    return SourceValidator(config)
```

Written in the shape of the existing `get_storage` / `get_config` providers. Injected into both
`add_source` and `update_source` as `Depends(get_validator)`; no inline construction survives.

The `try` is defence in depth, and its original justification has been retired: it claimed an
uncaught config raise would exit as a bare non-JSON 500. It would not — every route resolving
`get_validator` also carries `Depends(verify_api_key)`, which loads the same config and raises
`ServerError` first, and that has a registered handler. What survives is the Principle II reason:
an unloadable config should degrade only the path that needs it.

## Files

| File | Change |
|---|---|
| `daemon/src/prismis_daemon/validator.py` | `__init__(config: Config \| None = None)`; split `_validate_reddit` into parse/probe/interpret; delete the `about.json` httpx call; credential gate; clamped session |
| `daemon/src/prismis_daemon/api.py` | add `get_validator`; inject at `add_source` and `update_source`; wrap both `validate_source` calls in `asyncio.wait_for(asyncio.to_thread(...))` |
| `daemon/tests/unit/test_reddit_validation_unit.py` | **new** — parse, interpret, credential guards. No network, no credentials. |
| `daemon/tests/integration/test_api_integration.py` | SC-7 file-source endpoint test; SC-11 unloadable-config test |
| `daemon/tests/integration/test_validator_integration.py` | rewrite the reddit cases; update the six now-false skip reasons |

## Alternatives Considered

**A1 — keep httpx, add an OAuth token by hand.** Rejected: reimplements what PRAW already does
correctly, and the daemon must hold a second auth path that drifts from the fetcher's. P15 —
`fetchers/reddit.py` already establishes the pattern.

**A2 — make `Config` a required constructor argument.** Rejected: breaks 12 construction sites and
couples rss/youtube/file validation to a loadable config they never needed. P3 — optional is the
reversible shape.

**A3 — inject a fake validator for endpoint determinism (what #63 literally asks for).**
Rejected: the constitution permits mocking LLM providers "and the only one," and `SourceValidator`
is internal. Not a preference — a rule. Determinism comes from the `file` source path instead.

**A4 — set `PRAWCORE_TIMEOUT` at process start.** Rejected on evidence: it is read at import and
bound as a default argument, so it only works if set before the first prawcore import anywhere in
the process — an ordering constraint nothing enforces and any new import can break silently.

## Pick Justification

- **P16 (root cause over symptom)** — the unauthenticated endpoint cannot be repaired, only
  replaced; and the `env:` guard fixes the cause of the false "invalid credentials" rather than
  rewording the message.
- **P15 (existing patterns when they fit)** — `get_storage`/`get_config` for the provider,
  `fetchers/reddit.py` for the PRAW construction, `Config.validate()` for the `env:` check.
- **P3 (reversibility)** — optional config; the probe's clamp is additive.
- **Constitution Principle II** — the whole outcome table exists to stop four answers sharing one
  representation.
- **Constitution Principle I** — the module table above is written before implementation.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `verify.sh` never runs `test_validator_integration.py`, so the refactor can break its 6 construction sites and 1 private call invisibly | **HIGH** | SC-14 requires an explicit `PRISMIS_LIVE_NETWORK_TESTS=1` run, recorded in Outputs. Keep the `_validate_reddit` name. |
| Success path and 404/403/429 discrimination cannot be proven on this Mac | **HIGH** | Verify on cerebro with **both** env vars. The suite's autouse `isolated_xdg_env` means the real config is never read — credentials arrive only via env. |
| `asyncio.to_thread` + `wait_for` abandons rather than kills a slow thread | MEDIUM | Recorded as a known limitation; strictly better than today's unbounded blocking. |
| The clamped-session approach depends on prawcore passing `timeout=` explicitly | MEDIUM | Verified in `prawcore/sessions.py`. If it regresses, the outer `wait_for` still bounds the request. |
| A new untracked test file could carry a real secret | MEDIUM | SC-8 runs `git add -A` first, then greps the index. |

## Success Criteria

SC-1 through SC-14 in the work order. Mechanical checks on SC-1, SC-5, SC-8, SC-9.

**Demo command** (expected: every line PASS, gate exit 0):

```bash
cd /Users/rudy/development/projects/prismis
bash -c 'f=daemon/src/prismis_daemon/validator.py
  ! grep -q "about\.json" "$f" && grep -q "praw\.Reddit(" "$f" && grep -q "old\.reddit\.com/r/" "$f" \
  && echo "SC-1: PASS" || echo "SC-1: FAIL"'
(cd daemon && uv run pytest -q --no-header tests/unit/test_reddit_validation_unit.py)
./.specify/verify.sh; echo "gate exit: $?"
```

## Quality Gates

From `CLAUDE.md`'s Learned Patterns, all of which bite here:

1. **`./.specify/verify.sh` exits 0 with no `VERIFY_UNCOVERED` line.** Do not assert a check count.
2. **pyright covers only `src`.** `daemon/pyproject.toml` sets `include = ["src/prismis_daemon"]`,
   so a green `pyright(daemon)` says **nothing** about the new test file. Do not cite it as evidence
   for test correctness.
3. **Mutation-verify every replacement test.** Disable the behaviour under test, confirm red,
   restore. A test that still passes with the logic removed proves nothing. This applies to all
   eight outcome cases in SC-2.
4. **No line numbers from memory.** Open the file and read the line as you write the citation, and
   prefer the symbol name over a bare range.
5. **Write symbol references as prose, not call signatures.** `ERA001` reads
   `symbol_name (path/to/file.py)` in a comment as commented-out code and turns the gate red.
6. **`ruff check .`** clean in `daemon`, and `pyright` 0 errors.

## Settled — do not re-open

- PRAW over httpx. The unauthenticated endpoint 403s universally; verified across three subreddits.
- `Config` optional, not required.
- **No fake validator.** Constitution Principle I. If endpoint determinism seems to need one, use
  the `file` source path — that is the answer, not a blocker.
- Credentials never in the repo, never in CI. Live tests stay behind the `REDDIT_CLIENT_ID` skip.
- Keep the `_validate_reddit` method name.
- Fixing the blocking-in-async is in scope — the operator chose it over filing it.
