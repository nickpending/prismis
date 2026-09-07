---
id: wo-reddit-validation-seam
type: fix
project: prismis
status: active
complexity: 5
created: 2026-09-06
updated: 2026-09-07
plan_ref: docs/work/reddit-validation-seam/plan.md
---

## What

Fix gh #59 and re-scope gh #63 as one change in `daemon/`.

Replace `SourceValidator._validate_reddit`'s unauthenticated `httpx.get` to
`https://www.reddit.com/r/<name>/about.json` with a PRAW probe using the credentials
`fetchers/reddit.py` already holds, make the failure modes distinguishable per constitution
Principle II, deliver config through a `Depends` seam that follows the existing
`get_storage` / `get_config` pattern, bound the probe to the validator's stated timeout, and
give `POST /api/sources` deterministic coverage without mocking anything.

## Why

Reddit now returns 403 to every unauthenticated `about.json` request. Verified this session:
`r/programming`, `r/python`, `r/LocalLLaMA`, two attempts each, sending the validator's own
`User-Agent` — 403 on all six. `validator.py`'s 403 branch returns
`"Subreddit r/X is private"`, so **every public subreddit reports as private and no Reddit
source can be added at all.** `grep -c praw daemon/src/prismis_daemon/validator.py` returns 0
while `fetchers/reddit.py` builds `praw.Reddit(client_id=..., client_secret=...,
user_agent=...)` from config. The fetcher has credentials; the validator does not.

Seven distinct outcomes currently collapse into one message. Under Principle II (Failures Are
Distinguishable, NON-NEGOTIABLE) a refused credential, an absent subreddit, a private one, and
a rate limit are four different answers and must not share a representation. The
invalid-credentials case is not merely mislabelled — the current code has no way to express it
at all.

## Brief

- **Stakes:** Reddit sourcing is entirely broken today — not degraded, unusable. A wrong
  diagnosis here is worse than the outage: "is private" sends the operator to Reddit's
  settings for a problem that lives in their own credentials, their rate limit, or a subreddit
  that never existed.

- **Constraints:**
  - **Credentials never enter the repo and never enter CI.** Operator decision this session.
    The repo is public; `ci.yml` triggers on `push` and `pull_request`; live Reddit calls would
    make the gate depend on Reddit's uptime and rate limits, which is the exact defect class
    the last two days were spent removing. Live-credential tests stay behind the existing
    `REDDIT_CLIENT_ID` env skip and run on cerebro.
  - **No mocks.** `.specify/memory/constitution.md` Principle I — internal code is never mocked
    — and Project Constraints: LLM providers are "the one collaborator Principle I permits
    mocking, and the only one." `SourceValidator` is internal and Reddit is not an LLM.
    **#63 cannot be solved by injecting a fake validator. Do not write one.**
  - **`Config` must be optional on `SourceValidator`.** rss / youtube / file validation needs
    none, and **twelve** sites construct `SourceValidator()` bare — 2 in `api.py`, 3 in
    `tests/unit/test_validator_unit.py`, 1 in `tests/unit/test_validator_youtube_protocol_unit.py`,
    and 6 in `tests/integration/test_validator_integration.py`. One of those calls the private
    `_validate_reddit` directly (`test_validator_integration.py:137`).
  - **No new dependencies.** `praw` 7.8.1 and `prawcore` are already daemon dependencies.
  - **Follow the existing seam pattern**, do not invent one: `get_storage` and `get_config` in
    `api.py`; `get_config` is already consumed via `Depends` elsewhere in that file.
  - **The reddit path must not outlive the validator's stated timeout budget.**

- **Decisions taken before planning (do not re-open):**
  - **D-CONFIG.** `get_validator` catches a `Config.from_file()` failure and passes
    `config=None`. `api.py` registers exception handlers only for `APIError` and
    `RequestValidationError`, and FastAPI resolves dependencies before the handler body, so an
    uncaught config raise leaves as a bare non-JSON 500 and breaks the
    `{success, message, data}` envelope the TUI and CLI parse. Under Principle II an unloadable
    config should degrade only the path that needs it; the reddit path then returns its own
    distinguishable "not configured" message.
  - **D-TIMEOUT.** Bound the PRAW probe to the validator's timeout: override prawcore's 16-second
    default and its 3-attempt retry strategy, and pass `check_for_updates=False` so
    `praw.Reddit.__init__` does not reach `pypi.org` via `update_checker`. Run the blocking probe
    off the event loop so one slow subreddit cannot stall concurrent API requests. The blocking
    call predates this work — the current `httpx.get` has the same defect — and the operator
    chose to fix it here rather than file it.
  - **D-ENVGUARD.** Treat an empty **or `env:`-prefixed** credential as unconfigured, before any
    network call. `expand_env_var` is `os.environ.get(env_var, value)`, so an unset variable
    yields the literal truthy string `"env:REDDIT_CLIENT_ID"`; a naive truthiness check hands
    PRAW a garbage id and reports "credentials invalid" on a machine that has none. Reuse the
    `startswith("env:")` shape `Config.validate()` already uses.

## Success Criteria

### SC-1: Reddit validation authenticates, and parsing survives
- **Given**: `daemon/src/prismis_daemon/validator.py` after the change
- **When**: the reddit path is inspected
- **Then**: the `about.json` request is gone and the reddit path constructs `praw.Reddit`
- **And**: the parse step still recognises `reddit.com/r/NAME` and `old.reddit.com/r/NAME`,
  which SC-6 requires — the bare domain string must NOT be treated as the thing to remove
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  f=daemon/src/prismis_daemon/validator.py
  if ! grep -q 'about\.json' "$f" && grep -q 'praw\.Reddit(' "$f" \
     && grep -q 'old\.reddit\.com/r/' "$f"; then
    echo "SC-1: PASS"; else echo "SC-1: FAIL"; fi
  ```

### SC-2: The seven Reddit outcomes are distinguishable through the public entry point
- **Given**: `validate_source(url, "reddit")` — the public method, **not** the interpret helper
  in isolation
- **When**: each of `NotFound`, `Redirect`, `Forbidden`, `UnavailableForLegalReasons`,
  `TooManyRequests`, a `ResponseException` with `response.status_code == 401`, and
  `RequestException` occurs
- **Then**: each returns a distinct message naming its own cause, and none returns the generic
  `Validation failed: …` from the outer `except Exception`
- **And**: an eighth, deliberately unenumerated prawcore exception is also asserted, because
  `validator.py`'s outer catch-all collapses anything the probe fails to route and
  `daemon/pyproject.toml` suppresses BLE001 across `**/*.py`, so ruff will never surface it
- **And**: no returned message contains any part of `reddit_client_id` or
  `reddit_client_secret` — the constitution's Security Requirements forbid secrets in error
  messages, and prawcore exception text flows into the 422 body

### SC-3: Invalid credentials are named as such
- **Given**: a `praw.Reddit` built with credentials that are syntactically present but not valid
- **When**: a subreddit is validated
- **Then**: the message identifies the credentials as invalid or expired, not the subreddit as
  private
- **And**: covered by a real test needing **no valid secret** — garbage credentials produce
  `ResponseException('received 401 HTTP response')` with `status_code 401` on demand, verified
  by execution this session

### SC-4: The seam is optional and every existing construction still works
- **Given**: `SourceValidator()` constructed with no arguments
- **When**: an rss, youtube, or file source is validated
- **Then**: it behaves as before, and all twelve existing bare-construction sites still work
- **And**: `daemon/tests/unit/test_validator_unit.py` and
  `test_validator_youtube_protocol_unit.py` pass unchanged

### SC-5: The API injects the validator at both sites
- **Given**: `daemon/src/prismis_daemon/api.py`
- **When**: `add_source` and `update_source` are inspected
- **Then**: neither constructs a `SourceValidator` in its body — the only construction in the
  file is the one inside `get_validator` — and both take it as a parameter defaulted to
  `Depends(get_validator)`
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  f=daemon/src/prismis_daemon/api.py
  n=$(grep -cE 'SourceValidator\(' "$f" || true)      # ANY construction, not just bare
  d=$(grep -c 'Depends(get_validator)' "$f" || true)  # one per handler signature
  if [ "$n" = "1" ] && [ "$d" = "2" ] && grep -qE 'def get_validator' "$f"; then
    echo "SC-5: PASS"; else echo "SC-5: FAIL (n=$n constructions, d=$d injections)"; fi
  ```

### SC-6: Parse and interpret are pure and covered without network
- **Given**: the reddit path split into parse (URL → subreddit name), probe (network), and
  interpret (outcome → result tuple)
- **When**: the parse and interpret tests run with no network and no credentials
- **Then**: they pass, and they are **not** gated behind `PRISMIS_LIVE_NETWORK_TESTS` or
  `REDDIT_CLIENT_ID`
- **And**: parse coverage includes every URL form the current code handles — `reddit://NAME`,
  `reddit.com/r/NAME`, `old.reddit.com/r/NAME`, a bare name, and an unparseable input

### SC-7: POST /api/sources has deterministic endpoint coverage
- **Given**: a `file` source URL, whose validation is pure string work — extension check plus
  scheme check, no network
- **When**: `POST /api/sources` is exercised end to end against real SQLite
- **Then**: the source is created and readable back, with no mock and no third-party call
- **And**: the test does not patch, stub, or fake `SourceValidator`, `Storage`, or `APIClient`

### SC-8: No credential reaches the repo or CI
- **Given**: the change **staged** (`git add -A`), so new untracked test files are in scope —
  `git grep` searches only tracked content and would miss a fresh file holding a secret
- **When**: the index and the workflow are inspected
- **Then**: no credential value is staged, no `praw.ini` is added, `ci.yml` gains no secret
  reference, and every test needing real credentials stays behind the existing
  `REDDIT_CLIENT_ID` env skip
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  git add -A
  bad=$(git grep --cached -inE \
    "(client_id|client_secret|refresh_token)[[:space:]]*[=:][[:space:]]*[\"'][^\"'\$]{8,}" \
    -- . ':!Makefile' ':!*.md' | grep -v 'your-reddit\|env:' || true)
  ini=$(git ls-files --cached | grep -c 'praw\.ini' || true)
  if [ -z "$bad" ] && [ "$ini" = "0" ] && ! grep -q 'secrets\.' .github/workflows/ci.yml; then
    echo "SC-8: PASS"; else echo "SC-8: FAIL"; printf '%s\n' "$bad"; fi
  ```

### SC-9: The gate passes
- **Given**: the completed change
- **When**: `./.specify/verify.sh` runs
- **Then**: it exits 0 and prints **no** `VERIFY_UNCOVERED` line — the check `ci.yml` actually
  enforces. Do not assert a fixed check count; the number is derived from runtime discovery and
  goes stale the moment a unit is added.
- **And**: CI is green on `main` after the push

### SC-10: Unconfigured credentials never reach the network
- **Given**: a `Config` whose `reddit_client_id` or `reddit_client_secret` is empty **or still
  carries the `env:` placeholder** that `defaults.py` ships
- **When**: a reddit source is validated
- **Then**: it returns "Reddit credentials not configured" with **no outbound request**,
  distinct from the invalid-credentials message of SC-3
- **And**: the same holds for `config=None`, and a unit test asserts both with no network

### SC-11: The seam does not make config a precondition for non-reddit CRUD
- **Given**: an install where `Config.from_file()` raises — no config file, or an outdated
  `[llm]` section
- **When**: `POST /api/sources` adds an rss, youtube, or file source, and
  `PATCH /api/sources/{id}` changes only a name
- **Then**: both still succeed with the `{success, message, data}` envelope — never a bare 500,
  never a non-JSON body
- **And**: an integration test asserts this by overriding the autouse `isolated_xdg_env` seal,
  the way `test_dual_service_config_unit.py` already does

### SC-12: The reddit probe is bounded by the validator's own budget
- **Given**: the PRAW client the validator builds
- **When**: its timeout and retry configuration are inspected
- **Then**: one reddit validation is bounded at or below `SourceValidator.timeout`, with
  prawcore's 16-second default and 3-attempt retry strategy explicitly overridden, and
  `check_for_updates=False` passed so construction does not reach `pypi.org`
- **And**: the class docstring's timeout claim is true for the reddit path, and the existing
  timeout test asserts the bound the reddit path actually honours rather than an unused attribute
- **And**: the blocking probe does not run on the event loop — `add_source` is `async def`, and
  a slow subreddit must not stall concurrent requests

### SC-13: The reddit success path still names the source
- **Given**: a valid subreddit and working credentials
- **When**: `validate_source(url, "reddit")` succeeds
- **Then**: the metadata dict still carries `display_name` from the subreddit's prefixed display
  name, so `api.py`'s `add_source` keeps naming the source `r/Name` rather than falling back to
  `extract_name_from_url`
- **And**: covered by the credential-gated test that runs on cerebro

### SC-14: The refactor does not break the skipped validator suite
- **Given**: `daemon/tests/integration/test_validator_integration.py`, which holds 6 of the 12
  bare construction sites and one direct `_validate_reddit` call at line 137
- **When**: it runs with `PRISMIS_LIVE_NETWORK_TESTS=1`
- **Then**: no test errors with `AttributeError` or `TypeError` from the parse/probe/interpret
  split — `verify.sh` never executes these, so the gate cannot catch it
- **And**: every skip reason citing gh #59's unauthenticated-403 explanation is updated or the
  test deleted; once #59 closes the daemon makes no unauthenticated request and that sentence is
  false in the tree (CLAUDE.md citation hygiene)
- **And**: this run is recorded in Outputs

## Out of Scope

- The OpenAI SDK migration (`docs/work/wo-openai-sdk-migration.md`).
- gh #61 (`since_hours` same-day filter), #65 (CLI/daemon normalization divergence),
  #58 (blind `except Exception` handlers — but see SC-2, which forbids relying on the catch-all
  for the reddit path), #64 (intermittent `test_concurrent_source_adds`).
- Restoring the other live-network integration tests (#60) — only the reddit validator tests
  are touched here.
- The verify chain over the nine pipeline links — separate work.
- `praw.ini` being read relative to the process CWD (`praw/config.py`), ambient input the
  conftest XDG seal does not reach. `fetchers/reddit.py` already carries this. **Needs a gh
  issue before this work order closes** — do not drop it silently.

## Approach

[Empty — filled after planning]

## Verification

```bash
cd /Users/rudy/development/projects/prismis

# the gate — >2 min, raise the Bash timeout
./.specify/verify.sh; echo "exit: $?"

# the credential-free tests. NOTE: do NOT run test_api_integration.py in full — it contains
# non-skipped tests that hit simonwillison.net, xkcd, feeds.bbci.co.uk, hnrss.org and
# 192.0.2.1, so a full-file run makes this verification depend on third-party uptime.
(cd daemon && uv run pytest -q --no-header \
    tests/unit/test_validator_unit.py \
    tests/unit/test_validator_youtube_protocol_unit.py \
    -k "not live")

# confirm the credential-gated tests still SKIP with no credentials present
(cd daemon && env -u REDDIT_CLIENT_ID -u REDDIT_CLIENT_SECRET \
    uv run pytest -q --no-header -rs tests/integration/test_reddit_fetcher_integration.py)
```

Cases that cannot be proven on this Mac — it has no daemon config
(`~/.config/prismis/config.toml` is a `[remote]`-only CLI stub; `Config.from_file()` raises
"Config [llm] section outdated") — verify on cerebro.

**The suite never reads the machine's real config.** `daemon/tests/conftest.py`'s autouse
`isolated_xdg_env` repoints `XDG_CONFIG_HOME` at a tmp dir holding the production template,
whose reddit section is `client_id = "env:REDDIT_CLIENT_ID"`. Credentials therefore reach the
suite **only** through the environment, and **both** variables are required — the skip guard
checks only `REDDIT_CLIENT_ID`, so setting one un-skips the test and then fails it on a 401.

```bash
ssh cerebro
cd ~/prismis && git fetch && git checkout <sha>
cd daemon
# both vars, sourced from cerebro's own config — never echoed, never written to the repo
export REDDIT_CLIENT_ID=$(python3 -c "import tomllib;print(tomllib.load(open('$HOME/.config/prismis/config.toml','rb'))['reddit']['client_id'])")
export REDDIT_CLIENT_SECRET=$(python3 -c "import tomllib;print(tomllib.load(open('$HOME/.config/prismis/config.toml','rb'))['reddit']['client_secret'])")
XDG_DATA_HOME=$(mktemp -d) PRISMIS_LIVE_NETWORK_TESTS=1 \
  ~/.local/bin/uv run pytest -q -rs tests/integration/test_validator_integration.py
# uv is at ~/.local/bin/uv and is NOT on the non-interactive ssh PATH
```

## Outputs

[Empty — filled on completion]
