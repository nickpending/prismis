---
type: architecture
subtype: decisions
project: "prismis"
status: active
created: "2026-04-07"
updated: "2026-06-12"
last_change: "baseline refresh — added two decisions reconciled from recent commits: source-type exclusion from deep extraction (1e39916, deep_extract_exclude) and TUI/web quote unification (54eadb4, single deduped Quotes section)"
tags: [architecture, decisions]
---

# Decisions

Architectural decisions and their rationale. Most recent first.

## [2026-06-03]: Source-type exclusion from deep extraction (commit 1e39916)

**Context:** Deep extraction (second-tier LLM synthesis) was gated only by priority via `auto_extract` ("none"|"high"|"all"). Some source types — Reddit in particular — are low signal-to-noise, where the deep synthesis (Counterintuitive / Buried lede / So what / Pushback) adds little value but still costs an LLM call. There was no way to opt a whole source type out of deep synthesis independent of its evaluated priority.

**Choice:** Add `Config.deep_extract_exclude: list[str]` (loaded from `[llm] deep_extract_exclude`, `default_factory=list` in code; the config template in `defaults.py` ships `deep_extract_exclude = ["reddit"]`). `orchestrator._should_deep_extract(priority, auto_extract, source_type, exclude)` gained two new params and short-circuits to `False` when `source_type in exclude` BEFORE the priority/threshold gating — so an excluded source skips deep synthesis regardless of priority. The call site (orchestrator.py:315-319) passes the item's `source_type` plus `self.config.deep_extract_exclude`. The same commit also fixed an invalid-TOML default (`high_read = null` → omit key, read via `.get()`) that made a fresh `make install-config` produce an unparseable config.

**Why:** Cost/value — exclusion is a per-source-type policy distinct from the per-item priority gate, so it belongs as its own config dimension rather than overloading `auto_extract`. The short-circuit runs first because exclusion is unconditional (cheapest check, no priority dependency).

## [2026-06-04]: Unify article quotes and deep quotables into one section (commit 54eadb4)

**Context:** The TUI reader and web detail panel each rendered light article quotes ("Key Quotes" / "Notable Lines") and deep-extraction quotables as two separate sections, which read as jarring duplication when both were present. The web never showed light quotes at all, and the TUI copy command pulled only `reading_summary`, omitting the deep synthesis prose.

**Choice:** Render `Summary → Deep Synthesis → one "Quotes" section` on both surfaces. In `reader.go`, `appendDeepExtractionSection` was split into `appendSynthesisSection` (prose only) + `combineQuotes` (dedup light + deep) + `appendQuotesSection` (single "## Quotes" block); `injectQuotesIntoSummary` was removed. `model.go` copy now emits `reading_summary + deep synthesis prose` (no quotes). `index.html` drops "Notable Lines" for a single `Set`-deduped Quotes block (now including light quotes), CSS `notable-lines` → `detail-quotes`.

**Why:** One canonical Quotes section eliminates duplication and brings parity between the TUI and web surfaces; deduplication prevents the same line appearing twice when it is both a light quote and a deep quotable.

## [2026-05-21]: extractor.extract() wrapped in asyncio.to_thread() to free FastAPI event loop (task 1.5)

**Context:** Task 1.4 added an `asyncio.Lock` around the extract endpoint's critical section to prevent duplicate LLM calls on concurrent first-extract POSTs for the same content_id. The lock closed the cost-duplication race but did not address event-loop starvation: `extractor.extract()` remained a synchronous blocking call inside an `async def` FastAPI handler, blocking ALL other API routes (entries, search, sources, audio briefings) for the 5-30s extraction window. Single-user installs felt sluggish under concurrent extraction; the mal.casa remote daemon exhibited observable starvation under TUI :extract during fetch-cycle auto-extract. Task 1.4 test phase surfaced the concern; advisor returned Pause sub_mode=fix; propose-fix routed add-task (criteria 2+3 failed because api.py is in iteration-12 scope); operator chose strict-discipline Path B to land the fix in iteration 12.

**Choice:** Wrap the call in `asyncio.to_thread()`: `extraction = await asyncio.to_thread(extractor.extract, content=..., title=..., url=...)`. The stdlib (Python 3.9+) primitive schedules the callable on the default ThreadPoolExecutor and suspends the awaiting coroutine until completion. While the thread runs, the event loop serves other routes. The asyncio.Lock held by the calling coroutine in `async with _get_extract_lock(content_id):` REMAINS HELD across the suspension — asyncio.Lock is coroutine-scoped, not thread-scoped — so task 1.4's INV-EXTRACT-RACE-1 (one extractor call per content_id under concurrency) is preserved unchanged. Alternatives considered and rejected: `loop.run_in_executor(None, ...)` (boilerplate, no kwarg passthrough; requires functools.partial or lambda); `anyio.to_thread.run_sync()` (new dep — anyio is a FastAPI transitive but not a direct daemon dep; task brief prohibits new deps).

**Why:** P16 — lock alone is symptom-fix (cost duplication); event-loop starvation is the root cause of latency complaints under concurrent extraction. asyncio.to_thread() addresses cause. P15 — asyncio.to_thread is the stdlib FastAPI-compatible standard for "block sync I/O without blocking the loop"; no novel abstraction, no new dep. P9 — task 1.5 IS the tracking handle for a known concern surfaced by task 1.4's test phase.

**Side benefit:** Tester independently challenged the builder's LOW risk rating on exception routing and discovered the CircuitOpenError→503 branch had ZERO coverage from task 1.2 through task 1.4 — silent gap closed by task 1.5's `test_circuit_open_error_through_to_thread_returns_503`. Codified in quality.md as a forward-looking rule for future exception-branch coverage.

## [2026-05-21]: Per-content_id asyncio.Lock for POST /api/entries/{id}/extract (task 1.4)

**Context:** Task 1.2 documented a read-modify-write race on the `extract_entry` handler: two simultaneous POSTs for the same content_id both pass the idempotency check ("if 'deep_extraction' in analysis: return cached") because both reads complete before either write. Both then invoke the LLM extractor, each spending one extraction call. The window is bounded by LLM latency (~5-30s), wide enough for racing requests via concurrent TUI :extract / batch CLI / fetch-cycle auto-extract. Task 1.2 explicitly deferred fix-design to "operator decision." Iteration-12 architecture audit surfaced that no gh issue or named handle had been filed, violating P9 — the concern aged silently from task 1.2 to iteration close.

**Choice:** Per-content_id `asyncio.Lock` keyed in a module-level `_extract_locks: dict[str, asyncio.Lock]` registry in `api.py`. The handler acquires the lock before the idempotency check and releases on `async with` exit, holding it across the LLM call and write-back. Registry pruned via `_extract_locks.pop(content_id, None)` after write-back to bound memory growth. Follows the `_breakers` module-level keyed-registry pattern from `circuit_breaker.py:175-182`. Alternatives considered and rejected: DB-level advisory lock (heavier, requires sqlite-vec support verification), idempotency token (symptom-fix only — treats deduplication, not the underlying race).

**Why:** P16 — lock is the root cause (no serialization), not symptom (cost duplication). The idempotency-token alternative treats the symptom by deduplicating by client-supplied token without preventing the LLM from being invoked twice on raceful first-extract POSTs. P15 — `asyncio.Lock` is the stdlib FastAPI-compatible primitive; the registry pattern already exists in circuit_breaker.py. P9 — the task itself is the tracking handle for a known defect that had no named handle.

**Follow-on (task 1.5):** The lock prevents double LLM calls but does NOT address event-loop starvation: `extractor.extract()` remains a blocking sync call inside an `async def` handler, blocking other API routes for the full extraction window (5-30s). Task 1.5 wraps in `asyncio.to_thread()` so the lock semantics are preserved (coroutine-scoped) while the blocking call moves off the event loop thread.

## [2026-05-20]: Cerebro deep extraction ENABLED — supersedes 2026-04-29 light-only stub-state policy

**Context:** Cerebro `host.json` notes[8] (2026-04-29 partial-deploy stub state) said `llm_deep_service` was "intentionally not configured — daemon logs 'Deep service: not configured' and runs in light-only mode." Three weeks later, the full iter-12 wave (tasks 1.1–1.3, 2.1–2.14, 3.1) was deployed and verified, and task 3.1 (`prismis-cli extract`) shipped the consumer interface for backfilling deep extractions on existing content. The "intentionally not configured" policy was the partial-deploy stub state, not a long-term decision.

**Choice:** Three cerebro-side config edits at 17:33 UTC:
1. `~/.config/prismis/config.toml [llm]` — added `deep_service = "prismis-openai-deep"` (was absent entirely; only `light_service` was set).
2. `~/.config/prismis/config.toml [llm]` — added `auto_extract = "high"` so the daemon's fetch cycles auto-deep-extract HIGH-priority items as they're scored (default was `"none"`).
3. `~/.config/llm-core/services.toml [services.prismis-openai-deep]` — changed `key = "sable-openai"` (migrate-config default referencing a key only present in the operator's malcasa apiconf, NOT cerebro's) → `key = "openai"` (the existing apiconf key the light service also uses).

After restart, daemon logs confirmed `✅ Light service: prismis-openai (ok)` + `✅ Deep service: prismis-openai-deep (ok)`. SC-12/SC-13 of task 3.1 verified end-to-end live from operator's CLI: `prismis-cli extract --priority high --limit 3` returns `Done: 3 extracted, 0 failed`; verification pipe confirms 3/3 items have `analysis.deep_extraction`; idempotent re-run picks up next 3 unextracted items.

**Why:** Cerebro keeps ONE OpenAI key for prismis (per-service separation lives in the operator's malcasa apiconf, not on cerebro). The migrate-config-emitted `sable-openai` key name was a stub for that scheme; the principled choice on cerebro is to point both light and deep at the existing `openai` key. The 2026-04-29 light-only policy reflected the partial-deploy state where the deep extractor pipeline existed but the consumer surfaces (TUI display from task 1.3, CLI command from task 3.1) hadn't all landed. With both surfaces shipped and the operator's TUI binary rebuilt from HEAD (`make install-tui`), turning deep on is the obvious closure.

**Operator workflow effect:** HIGH-priority items now auto-deep-extract during fetch cycles. `prismis-cli extract --priority high --limit N` backfills for items that predate enablement. The TUI's reader renders synthesis + quotables on items that have `deep_extraction` populated. gh #28 closed by enablement (was filed as the P9 deferred-validation handle yesterday).

## [2026-05-19]: pyright provisioned as daemon type-checker (basic mode + baseline yardstick)

Task 2.14 SC-39 required mechanical verification that the new `@typing.overload` stubs on `_rfc3339` narrow correctly. Quality.md previously listed Python type checking as "None currently configured — optional, mypy or pyright could be added"; no type-checker existed on the daemon.

**Choice:** pyright 1.1.409, installed via `uv add --dev pyright` (vendors Node binary through nodeenv). Configured in `daemon/pyproject.toml` under `[tool.pyright]` with `typeCheckingMode = "basic"`, `pythonVersion = "3.13"`, include = `src/prismis_daemon`. Run: `cd daemon && uv run pyright`.

**Why pyright over mypy:**
- Pyright's `@overload` narrowing is well-regarded and handled the `_rfc3339` stubs correctly out of the box (`uv run pyright src/prismis_daemon/api_models.py` returns `0 errors`); mypy has historical pain points with overload edge cases.
- ~10× faster than mypy on equivalent codebases; matters for ad-hoc CLI runs.
- Same engine as Pylance; free editor integration.

**Why basic mode (not strict):** standing up a type-checker on an existing codebase surfaces a backlog of latent annotation issues. Basic mode catches genuine defects (one real bug — `validator.py:215` 4-tuple/3-tuple mismatch — surfaced and fixed in the same session, gh #27) without drowning the operator in strict-mode noise. The 18-error daemon-wide baseline is documented in `quality.md ## Learned Patterns` as a regression yardstick: any task increasing the count introduces a defect; fixing pre-existing items is welcome but not required and should land via a tracked handle.

**Why now (not deferred):** SC-39 closure was the trigger, but the broader value is that future iterations gain mechanical type discipline on the daemon component for the cost of one config block plus a single dev-dep.

## [2026-05-18]: Pre-deploy validation — observability JSONL ts wire shape

Task 2.9 changed `observability.py:41` from `...Z` (naive ISO + literal `Z` suffix) to `...+00:00` (tz-aware ISO, explicit offset). Both are valid RFC3339 § 5.6. Audited external consumers on cerebro (the deploy target) before iteration-12 deploy, per task 2.13 spec, to close build-task-2.9 CONCERN[2] (external consumers not searched during 2.9 build).

Search commands run on cerebro (each cross-checks a different angle to mitigate the MEDIUM audit-miss risk):

- `ssh cerebro "grep -rn '_events.jsonl' /etc/ ~/.config/ /usr/local/etc/ 2>/dev/null"` → empty (exit 2, no matches)
- `ssh cerebro "systemctl list-units --type=service --state=running | grep -iE 'vector|promtail|fluent|filebeat|logstash|loki|grafana|alertmanager'"` → empty (exit 1)
- `ssh cerebro "systemctl list-units --type=service --all | grep -iE 'vector|promtail|fluent|filebeat|logstash|loki|grafana|alertmanager|telegraf|otel|opentelemetry|datadog'"` → empty (exit 1; covers stopped/disabled units too)
- `ssh cerebro "find /etc /usr/local/etc /opt /var/lib -maxdepth 3 -type d -iname '*vector*' -o -iname '*promtail*' -o -iname '*fluent*' -o -iname '*filebeat*' -o -iname '*loki*' -o -iname '*logstash*' -o -iname '*telegraf*' -o -iname '*opentelemetry*'"` → empty (no shipper install directories)
- `ssh cerebro "ps -ef | grep -iE 'vector|promtail|fluent|filebeat|logstash|loki|grafana|telegraf|otel' | grep -v grep"` → empty (no shipper processes running)
- `ssh cerebro "grep -rln 'events\\.jsonl' /etc /usr/local/etc /opt /var/lib /home 2>/dev/null"` → only `/home/rudy/.cache/uv/archive-v0/*/prismis_daemon/observability.py` (uv build-cache copies of the daemon's own *producer* source — not consumer code)
- `ssh cerebro "ls -la /etc/systemd/system/ /etc/systemd/user/ | grep -iE 'log|metric|telem|observ|prismis'"` → only `prismis.service` (the producer unit itself)
- `ssh cerebro "ls ~/.local/share/prismis/observability/ | tail -10"` → confirms cerebro is actively producing JSONL (10 daily files through 2026-05-18); producer side is live and emitting

| Consumer | Config path | Parser behavior | Accepts +00:00? |
|----------|-------------|-----------------|-----------------|
| (none enumerated) | n/a | n/a | n/a |

**Scope correction (2026-05-18, post-audit).** The 8-vector SSH search above only verifies the absence of *on-cerebro* consumers (shipper processes, systemd units, local readers). The original audit framing claimed "external consumers = 0" but only checked one host. The remaining scope — network-side consumers that could pull cerebro's JSONL (NFS mount, rsync cron, syslog forward, seq pull-ingester on mystique or destiny) — was not verified by the SSH search.

**Two independent grounding sources close the network scope:**

1. **Producer code (read at `daemon/src/prismis_daemon/observability.py:38-54`):** the daemon's emission path is `open(log_file, "a")` followed by `fcntl.flock` + `f.write(json.dumps(entry) + "\n")`. No HTTP client, no socket, no seq POST, no remote shipper. Events live on cerebro disk only. For any network host to consume these events, it would have to PULL the JSONL — the daemon never pushes.
2. **Operator confirmation (Rudy, 2026-05-18):** "no nothing reads prismis/cerebro observability today." No NFS mount, no rsync, no syslog forward, no seq ingester pulling from cerebro. Mystique and destiny run seq for other purposes; neither is configured to ingest cerebro's prismis events.

Decision: DEPLOY AS-IS — operator-confirmed no network consumer reads cerebro's `_events.jsonl` today, and producer code proves no daemon-side push exists, so no consumer can hard-parse `Z`. RFC3339 wire shape change at `observability.py:41` (Z → +00:00) is deploy-safe.

Rationale: empty consumer set closes CONCERN[2] without code change. INV-OBS-1 holds vacuously (no consumers to break). INV-FETCH-1 remains active and unchanged. **Point-in-time caveat:** this validation is dated 2026-05-18. If a consumer (e.g., a seq pull-ingester, rsync target, or NFS mount of `~/.local/share/prismis/observability/`) is added later, that consumer must accept RFC3339-general — or this validation lapses and the rollback path in task 2.13 SC-40 must be revisited. The audit document IS the named handle (P9) that resolves the open CONCERN.

**Process note (2026-05-18):** The 2.13 task-builder bypassed the `/homenet` operations workflow. Should have read cerebro's `~/.config/homenet/host.json` manifest first (authoritative service inventory) and oriented from `~/.local/share/homenet/inventory.jsonl` (network topology — would have surfaced destiny and mystique as observability hosts immediately). Cowboy `ssh cerebro ...` SSH bypasses captured *evidence* without orienting; orienting first would have framed the question network-scoped from the start. Orchestrator carries the routing responsibility — task-builder spawn prompts must point at registered skills when work crosses skill territory.

---

## API ↔ Consumers datetime wire format: RFC3339 via Pydantic encoders (entries + search closed; remaining endpoints tracked as 2.10/2.11/2.12)

**Update (task 2.9, 2026-05-07):** Producer-side cleanup closed. All 6 daemon `datetime.utcnow()` sites replaced with `datetime.now(UTC)` (5 fetcher emission sites at `reddit.py:384`, `rss.py:120`, `rss.py:200`, `youtube.py:450`, `youtube.py:486` + 1 observability site at `observability.py:41`). Builder also tightened `youtube.py:187` (`datetime.now() - timedelta(...)` for max-days-lookback math) from naive to `datetime.now(UTC)` per plan Step 7 invitation citing P15 + P16 (yt-dlp's `upload_date` is UTC-based; naive local time could cross day-boundaries on far-from-UTC TZs). New invariant **INV-FETCH-1** introduced and tested at `daemon/tests/unit/test_utcnow_fix_unit.py` (8 tests, all passing): no `datetime.utcnow()` in `daemon/src/prismis_daemon/fetchers/`. The `_rfc3339()` encoder's naive branch in `api_models.py` remains **load-bearing** for ~7100 pre-existing legacy storage rows written before 2.9 — the xfail at `test_rfc3339_helper_unit.py:173-224` documents this contract and stays until a backfill migration converts those rows. Three handles filed: gh #24 (youtube.py pre-existing S603 + B904 surfaced by claudex-guard strict mode), task 2.13 (pre-cerebro-deploy validation that external observability consumers accept the `Z`→`+00:00` JSONL `ts` shape change), and a CONCERN inside task 2.9's spec for the backfill operator-decision. The 2.7/2.8/2.9 trifecta now closes the wire contract end-to-end at producer (2.9), serializer (2.8), and consumer-parser (2.7) layers.

**Update (task 2.8, 2026-05-06):** The list-endpoint half of this decision is now closed. `/api/entries` and `/api/search` route through `ContentResponse(...).model_dump(mode="json")` (api.py refactor); `ContentItemModel` declares all 21 fields with `@field_serializer` decorators on every datetime field delegating to `_rfc3339`. `SourceResponse` and `AudioBriefingResponse` migrated from V1-compat `json_encoders` to V2-native `@field_serializer` in the same edit pass — `api_models.py` now contains zero `json_encoders` references, eliminating the silent-break-on-Pydantic-V3-upgrade risk for response-side datetime serializers. INV-API-TS-4 (Pydantic-routed-only contract for content endpoints) is active in `boundaries.md`. Test-runner's independent api.py audit (grep `return {` across 13 raw-dict sites) caught two parallel structural defects neither plan nor build flagged: `/api/audio/briefing` audio endpoint bypass (task 2.10) and `/api/entries/{content_id}` detail endpoint bypass (task 2.11). `SourceRequest.validate_url` still uses the V1 `@validator` decorator — same V3-removal risk class on the request side; tracked as task 2.12. The xfail tests T-I and T-J at `daemon/tests/integration/test_content_response_api_integration.py:251-343` carry `strict=True` and stage to PASS once 2.10/2.11 land — load-bearing CI signal preserved through the gap. Task 2.9 (fetcher `datetime.utcnow()` cleanup) remains independent.

**Update (task 2.12, 2026-05-18):** Request side closed. `SourceRequest.validate_url` migrated from `@validator` to `@field_validator('url', mode='before')`; `validator` import removed. `api_models.py` now contains zero V1-compat Pydantic mechanisms (zero `@validator`, zero `json_encoders`). PydanticDeprecatedSince20 warning count = 0 post-build. New structural invariant **INV-VAL-2** (`@validator` count = 0) added to `test_source_request_validator_unit.py::test_api_models_zero_v1_validator_decorator`; behavioral invariants (empty rejection, whitespace-only rejection, whitespace strip) covered by T-A/B/C in same file. Test-runner caught a coverage gap that plan and build missed — the existing `test_source_validation_blocks_invalid` integration test was named for URL-validation but actually exercised source-type validation; renamed to `test_source_type_validation_blocks_invalid` with corrected docstring (test_api_integration.py:98). The silent-break-on-Pydantic-V3-upgrade risk class is now closed for `api_models.py` end-to-end (response + request).



**Context:** Task 1.2 r4 (commit `5d097dd`, 2026-04-27) flipped `daemon/storage.py` datetime binding from naive datetime objects to `datetime.now(timezone.utc).isoformat()` ISO strings — a correct fix at the storage layer for the Python 3.12 sqlite3 deprecation, but it silently changed the wire format on the API/TUI boundary. Storage moved from space-separator (sqlite default adapter) to `T`-separator. The TUI's `apiTime.UnmarshalJSON` at `tui/internal/api/client.go:118-124` was written 2025-11-05 against the old space-separator and broke on the new format. Rudy hit `failed to parse time "2026-05-05T03:43:08+00:00" as "2006-01-02 15:04:05"` on 2026-05-04 during routine TUI use. Root cause was an implicit wire contract — Pydantic's default datetime serializer leaked whatever shape flowed through from storage; any storage-layer change silently shifted the wire. Three options weighed: (A) consumer-leniency in TUI (rejected — symptom workaround per P16), (B) Pydantic field encoders at the API boundary (chosen), (C) storage-layer normalization (rejected — wrong layer; storage is internal to the daemon).

**Choice:** Pin the API ↔ Consumers datetime wire contract to RFC3339 — `T` separator, explicit timezone offset (`+00:00` or `Z`) — and enforce it at the API boundary via Pydantic encoders. Implementation lands across three tasks: task 2.7 (this) added a shared `_rfc3339(v)` helper in `daemon/src/prismis_daemon/api_models.py` that handles both naive (appends `Z`) and tz-aware (uses `.isoformat()` directly) shapes, applied via `json_encoders` to `SourceResponse` and `AudioBriefingResponse`; collapsed `tui/internal/api/client.go` `apiTime.UnmarshalJSON` to a single `time.Parse(time.RFC3339, s)` (matches the project pattern at `tui/internal/db/queries.go:141, 311, 422, 525, 638`); fixed `api.py:1333` from naive `datetime.now()` to `datetime.now(UTC).isoformat()` for `AudioBriefingResponse.generated_at`; added the contract to `boundaries.md` as INV-API-TS-1/2/3. Task 2.8 finishes the boundary at `/api/entries` and `/api/search` — both currently bypass Pydantic (raw-dict response paths at `api.py:870-895`); the new `ContentResponse` + `ContentItemModel` Pydantic models route them through the same `_rfc3339` encoder via `@field_serializer` decorators, and migrate the existing `SourceResponse` + `AudioBriefingResponse` from V1-compat `json_encoders` to V2-native decorators in the same pass (closes the silent-break-on-Pydantic-V3-upgrade class of failure). Task 2.9 fixes the upstream producer — 6 daemon `datetime.utcnow()` sites (5 fetcher + observability.py:41) replaced with `datetime.now(UTC)` so storage emits tz-aware values per `decisions.md` "Storage datetime ISO-string convention" (this entry's pair). Together: task 2.7 mechanizes the consumer-side parser strict + boundary doc; task 2.8 mechanizes the producer-side wire enforcement at the only two endpoints that bypassed Pydantic; task 2.9 cleans the upstream emission convention. INV-API-TS-1/2/3 introduced this task; INV-API-TS-4 (Pydantic-routed-only contract for content endpoints) introduced by task 2.8.

**Why:** P16 (root cause over symptom) — the wire contract was implicit, which is what allowed a correct storage-layer change to silently break consumers. Mechanizing the boundary at the API serializer prevents the entire class of "storage shape leaks to wire and breaks consumers." Consumer-leniency (Option A) would have absorbed the ambiguity instead of removing it; storage normalization (Option C) is the wrong layer because storage is internal to the daemon and consumers should not have to track its representation. P15 (existing patterns) — `tui/internal/db/queries.go` already uses `time.Parse(time.RFC3339, ...)` at 5 sites; `_rfc3339()` extends the project's existing `json_encoders` convention from `SourceResponse`. P3 (reversibility) — three local edits per task, each independently revertable. Task 2.7 closed `complete (partial demo)` with named handles to tasks 2.8 + 2.9 — not abandoned per P9; the SC-25b defect-proof test ships with `@pytest.mark.xfail(strict=True, reason="task 2.8 SC-29 — passes when /api/entries routes through ContentResponse")`, so the load-bearing CI signal is preserved through the gap and flips XPASSED→FAIL when 2.8 lands (canonical pytest pattern; prompts test-runner to remove the decorator and promote the test). Per task 2.7 build (2026-05-05) + Rudy's Option-A architectural pick after challenging the orchestrator's initial Option-D bias.

## Iteration-boundary git-dep pinning in daemon/pyproject.toml `[tool.uv.sources]`

**Context:** The 2026-04-29 task-2.3 cerebro deploy auto-bumped two git dependencies (`llm-core` 0.3.0 → 0.3.1 commit `2eb4429`, `apiconf` to commit `c8261726`) as side effects of `make install-daemon`. Root cause: the `llm-core` source declaration in `daemon/pyproject.toml` `[tool.uv.sources]` had no `rev=` field, and `uv tool install --reinstall` (the canonical install path) bypasses the lockfile pins in `daemon/uv.lock` for git sources. The lockfile said 0.3.0; the resolver fetched HEAD anyway. A future breaking HEAD commit in either repo would surface as a daemon failure with no prismis-side commit to attribute it to. propose-fix routed `add-task` because the non_blocking criterion failed: each subsequent deploy without the pin re-exposed the project.

**Choice:** Pin every `[tool.uv.sources]` git entry with a `rev=` field referencing a specific commit hash (INV-DEP-1). Codify the iteration-boundary pinning policy in an 8-line comment block above `[tool.uv.sources]` documenting the bump procedure (update `rev=`, run `uv lock`, commit pyproject.toml + uv.lock together with a message naming the new rev — INV-DEP-3). `daemon/uv.lock` MUST be committed alongside any pyproject.toml dependency change (INV-DEP-2). Apiconf is not directly declared — it flows in transitively via llm-core, so pinning llm-core subsumes the apiconf risk. The validated rev for the v0.3.1 cerebro state is `2eb4429` (full SHA `2eb4429bb0c7619dcbe1159ccd4720d4f4294f2a`). Five tomllib-based tests at `daemon/tests/unit/test_dep_pin_unit.py` mechanically protect INV-DEP-1 + SC-17/19; INV-DEP-2 commit-atomicity and INV-DEP-3 commit-message discipline are process-enforced (pre-commit hook is the appropriate mechanism, not pytest).

**Why:** P16 (root cause over symptom) — the missing `rev=` is the actual cause of resolver drift; alternatives like a post-install diff guard treat the symptom. P9 (no silent scope drops) — bumps become deliberate prismis-side commits visible in git log, not deploy-time side effects buried in install output. B3 (cite or don't claim) — the validated commit lives in human-authored source (`pyproject.toml`), not just the generated lockfile. P15 (existing patterns when they fit) — verification uses canonical `uv lock --check` + `uv tree | grep` (the plan originally cited a non-existent `uv tool install --dry-run` flag; the substitution lives in `quality.md` as the `uv resolver-verification idiom`). Per task 2.5 build (2026-05-04).

## SQL parameterization in storage.py: heredoc + `query += LITERAL` accretion

**Context:** Task 2.4 set out to eliminate the structural SQL-injection-shaped pattern in `Storage.get_feedback_statistics()` — four `execute(f"""...{time_filter}...""")` callsites where `time_filter` was an f-string interpolating `since_days`. The plan called for the minimal form `execute(f"""...{static_template}...""", time_params)` (outer f-string, bound parameter), which is structurally safe because `time_filter` becomes one of two static literals and `since_days` flows through bind. Build-time the PostToolUse hook (claudex-guard, which delegates SQL injection detection to ruff S608 per `python_patterns.py:414`) blocked the f-string form on all 4 sites in 3 successive Edit attempts. ruff S608 fires on shape (`execute(f"...")` and `query = "..." + var` at fresh assignment) regardless of whether the interpolation is provably safe. Builder pivoted to parenthesized adjacent-literal concat with mid-string `+ time_filter` — passes S608 but introduced a third concat style in storage.py and grew the function by ~30 lines. Operator pushback flagged this as worse-than-plan code quality. Post-build refactor to canonical heredoc + `query += LITERAL` accretion (matching `get_content_by_feedback` at `storage.py:2287-2324`) verified empirically: ruff S608 passes on `query += " AND ... ?"` (literal accretion), fails on `query = "..." + var` (variable concat at fresh assignment).

**Choice:** SQL parameterization in storage.py uses **heredoc base + conditional `query += " AND ... ?"` accretion + bound `params` list passed as second `execute()` argument**. Single concat style across the file. Avoid: `execute(f"...")` (S608 blocks), `query = "..." + var` at fresh assignment (S608 blocks), parenthesized adjacent-literal concat with mid-string `+ var` (passes S608 but ugly + bloats line count). Codified in `quality.md` ## Learned Patterns → ### prismis daemon (Python).

**Why:** P16 (root cause over symptom) — the root cause is f-string SQL with caller data; the canonical pattern eliminates the shape entirely (SQL text fully static, data flows via bind). Patching with `# noqa: S608` would be a symptom fix. P15 (existing patterns when they fit) — `get_content_by_feedback` proves the pattern; matching it gives the file one concat style instead of three. P1 (security) — defense-in-depth: parameterized form structurally safer for any future caller that bypasses the API's `int | None` Pydantic enforcement. Per task 2.4 build-task and post-build refactor 2026-04-30.

## Web card click delegation: structural scoping over per-element stopPropagation

**Context:** Task 1.3 added an article detail panel to the web view, with click-to-open behavior on `.content-item` cards. Initial implementation guarded the four button listeners (mark-read + vote in renderContent and renderTop3MustReads) with `e.stopPropagation()`. Test-task validation caught a 5th unguarded interactive element — the title `<a>` link — and codified the rule as INV-D3: "every interactive child of `.content-item` MUST call e.stopPropagation()." The pattern was structurally fragile: every future button or link added inside a card would need to remember the rule.

**Choice:** Restructure the click target instead of patching every interactive child. Removed `onclick` from `.content-item`, scoped it to `.item-summary` only. Title `<a>`, vote buttons, and mark-read button now live OUTSIDE the click region by structure, so bubble-up cannot reach `openDetail`. The four `stopPropagation` calls remain in place but no longer carry correctness load — they are defense-in-depth, not the primary guard.

**Why:** P16 (root cause over symptom) — the symptom was missing stopPropagation; the root cause was a click target that included interactive children. Narrowing the click surface eliminates the need for the per-element rule. INV-D3 reframed in `quality.md`: card-onclick scoped to non-interactive region, not "every interactive child must call stopPropagation." Per task 1.3 build resume.

## Daemon extract endpoint: cached-return runs before extractor-configured check

**Context:** Task 1.2 introduced POST /api/entries/{id}/extract with the order: extractor-None check (503 not-configured) → entry fetch → cached check → run extractor. In task 1.3 testing, INV-004 idempotency could not be observed in environments without `llm.deep_service` configured: every repeat POST returned 503 unconditionally because the not-configured check fired before the cached return. The advisor initially classified this as Pause/follow-up; user escalated to in-scope Fix.

**Choice:** Reorder the endpoint so entry fetch + cached return runs first, extractor-configured check second. Cached reads succeed regardless of deep_service config (they don't need the extractor). Combined with the 503 sub-code change: ServiceUnavailableError now carries `reason` ∈ {`not_configured`, `circuit_open`} in its response body so TUI/web clients can render distinct user messages.

**Why:** P16 (root cause over symptom) — the symptom was 503 ambiguity; the root cause was a branch order that conflated configuration with idempotency. The reorder makes INV-004 fully observable. P15 (existing patterns when they fit) — `reason` field extends the existing `ServiceUnavailableError` shape rather than inventing a new HTTP status code (501 was an alternative considered and rejected). Per task 1.3 build resume.

## Storage datetime ISO-string convention (Python 3.12 sqlite3 adapter)

**Context:** Python 3.12 deprecated the default sqlite3 datetime adapter; storage.py:243 and :391 surfaced 12 DeprecationWarnings per test run by passing datetime objects through the deprecated path. The deprecation predated task 1.2 but was untracked and surfaced during test-task-1.2's regression sweep. The `deep_extractor.py:156` already used the canonical replacement pattern `datetime.now(timezone.utc).isoformat()`. Initial inclination was to file the deprecation as a gh issue for consistency with gh#15 (Pydantic V1) and gh#16 (utcnow in observability.py), which were both filed for the same deprecation class. Operator pushback: storage.py was already in task 1.2's modification scope (added `update_analysis()`), and the fix is mechanical with an established project pattern — filing a gh issue creates parallel work for a 2-line edit.

**Choice:** Apply ISO-string conversion at the call site in storage.py (matches `deep_extractor.py:156` pattern) rather than registering global sqlite3 adapters. Bind datetime parameters as `datetime.now(timezone.utc).isoformat()` strings before passing to `conn.execute()`. Consistent across all datetime-binding call sites in storage.py.

**Why:** P15 (existing patterns when they fit) — the project already chose ISO strings; consistency over a global adapter registration. P16 (root cause over symptom) — fix the actual deprecation, not silence the warning. P3 (reversibility) — call-site change is local and grep-able; no module-init side effects to undo. The "in-scope file + mechanical fix + established pattern → apply inline" decision rule distinguishes this from gh#15 (api_models.py — out of scope) and gh#16 (observability.py — out of scope). Per task 1.2 r4 patch.

## Typed CircuitOpenError for 503 routing in extract_entry

**Context:** Task 1.2's plan r2 introduced `POST /api/entries/{id}/extract` and used a substring match (`'circuit' in str(e).lower() and 'open' in str(e).lower()`) to route circuit-open `RuntimeError` to HTTP 503 vs. generic 500. The plan acknowledged this as a same-file P3 deferral — "easy to harden later with a typed exception." Audit during build review found the rest of `api.py` routes errors by exception type in 12+ sites (the typed `api_errors` hierarchy exists for this purpose); the substring approach was the only instance in the daemon using string content to choose between status codes. Code was uncommitted, both files (`deep_extractor.py`, `api.py`) were already open in the build's resume bundle.

**Choice:** Add `class CircuitOpenError(RuntimeError)` to `deep_extractor.py`. Raise it (instead of plain `RuntimeError`) when `circuit.check_can_proceed()` is False. Replace the substring-match block in `api.py:extract_entry` with `except CircuitOpenError as e: raise ServiceUnavailableError(...)` followed by a generic `except Exception as e: raise ServerError(...)`. Verified by 8-test POST routing regression including 2 adversarial type-vs-substring pairs.

**Why:** P15 (existing patterns when they fit) — typed routing matches the convention everywhere else in api.py and the api_errors hierarchy. P16 (root cause over symptom) — eliminates the implicit string-content contract between `deep_extractor.py` and `api.py`. The "we're already opening both files" trigger collapsed the P3 deferral. Per task 1.2 r2 corrections.

## Standalone ContentDeepExtractor class (vs. inline orchestrator method)

**Context:** Deep extraction needed a home. Two shapes considered: (a) inline `_deep_extract()` method on `DaemonOrchestrator`, or (b) standalone `ContentDeepExtractor` class in its own module — same shape as `ContentSummarizer` and `ContentEvaluator`. The async API endpoint (`app.state.deep_extractor`) needs a shareable object; if the extractor lived as an orchestrator method, the API would need a full orchestrator reference in app.state.

**Choice:** Standalone class in `deep_extractor.py`. Instantiated in `__main__.py` (both `run_scheduler` and `--once` paths), injected into the orchestrator constructor as `deep_extractor: ContentDeepExtractor | None`, and into `app.state.deep_extractor` for the API endpoint. Single responsibility — extraction logic separate from pipeline coordination.

**Why:** P15 (existing patterns when they fit) — `summarizer.py` and `evaluator.py` are exactly this shape, so the pattern is already in the codebase twice. Testable in isolation. Shareable between pipeline and API without coupling them. The exploration spec (`obsidian/reference/technical/explorations/prismis/deep-extraction-two-tier-summarization.md`) explicitly closed the post-storage async alternative ("design decision, not post-storage patch"), so B1 (don't guess) drove the rejection of background-queue extraction. Per task 1.2 plan.

## Source-internal-only skepticism in deep extraction synthesis

**Context:** The first-pass deep extraction prompt (transcribed from the task spec into `deep_extractor.py:_system_prompt`) was a hype-man — it asked for what's counterintuitive, what's the so-what, what's quotable, but never asked the model to challenge weak claims. Operator review of three live syntheses (Anthropic Project Glasswing, Mongoose RCE, ARTEMIS pen-test) confirmed the model treated every source's claims as established truth, including vendor-self-published benchmarks and self-built-tool methodology. Adding "be skeptical" risked three failure modes: paranoid blanket distrust on every article, asking the reader to verify external claims, or relying on stale training-data knowledge to challenge facts (hallucination risk).

**Choice:** Add `**Pushback:**` as a fourth optional labeled section in the synthesis schema (alongside Counterintuitive / Buried lede / So what), with a hard guardrail: "Skepticism is source-internal only. Do not use outside knowledge to challenge claims, do not ask the reader to verify anything, do not go external. If the source is solid, skip Pushback entirely." Bake the exploration's worked example (Agent Teams parallel-execution synthesis) into the prompt as few-shot. Tighten quotables to 0–3 with a "stands on its own" quality gate. Tighten role to "sharp, skeptical analyst writing a private note to a smart friend... Read with a bullshit detector on."

**Why:** B1 (don't guess) — confining skepticism to the four corners of the document keeps the model out of fabrication territory. P15 — the worked example carries the labeled-section pattern via demonstration rather than instruction. Cost: ~25–30% longer output when Pushback fires; bounded by skip-when-not-warranted instruction. Verified live on 3 HIGH items: all three Pushback sections pulled real signals from inside the source (Anthropic's own "build buzz" caveat, Mongoose maintainer dysfunction, ARTEMIS author-built-tool COI). Per task 1.2 r3 prompt patch.

## Cosine similarity from L2 distance — fix at the formula layer (storage.py)

**Context:** `storage.py:1586` computed search similarity as `1.0 - L2_distance`, treating vec0's L2 distance as if it were cosine distance. The code comment claimed "cosine distance from vec_content," but `schema.sql:90-93` declares `vec_content USING vec0(... FLOAT[384])` with no `distance_metric=cosine` argument — vec0 defaults to L2. For unit vectors, cosine similarity is `1 - d²/2`, not `1 - d`. Discovered during task 2.1 testing when orthogonal unit vectors produced sim = −0.414. The defect was masked end-to-end because task 2.1 raised `min_score` default to 0.1 (filtering negatives at the API), but rankings were still semantically wrong for any mid-angle pair.
**Choice:** Patch the formula at `storage.py:1586` to `similarity = 1.0 - (float(candidate["distance"]) ** 2 / 2)`. Update the line 1585 comment to describe the L2-to-cosine conversion accurately. Embeddings ARE unit-normalized — verified via `~/.cache/.../all-MiniLM-L6-v2/.../modules.json` (Normalize layer at module index 2) and empirical L2 norms of 1.000000 across sample encodings. Schema unchanged; ingestion unchanged.
**Why:** Three viable fix paths existed. Option 1 (formula patch — chosen) is a single-line change with no migration. Option 2 (declare `distance_metric=cosine` in schema) would let vec0 return cosine distance directly but requires schema migration + re-embed of all stored content — disproportionate for a one-formula fix. Option 3 (`normalize_embeddings=True` at ingest) is redundant — the model's Normalize layer already does this. P16 (root cause) drove the choice: fix the actual formula, not the symptoms; P15 (existing patterns) preserved the existing test structure with corrected geometry; P3 (reversibility) — single-line revert restores prior behavior with zero data impact. Per task 2.2.

## Server-side search relevance floor (min_score default 0.1)

**Context:** Semantic search returned items with relevance scores as low as 0.002 — pure noise that eroded trust in search quality. Each consumer (TUI, CLI, prismis skill, lore passthrough) was independently working around this with its own filter (e.g., the prismis skill's `jq select(.relevance_score >= 0.15)`). Per-consumer filtering produced inconsistent thresholds across surfaces and forced every new caller to relearn the lesson.
**Choice:** Raise the `/api/search` endpoint's `min_score` default from `0.0` to `0.1` (`daemon/src/prismis_daemon/api.py:894-901`). Add a `--min-score` CLI flag (`cli/src/cli/search.py`, `cli/src/cli/api_client.py`) that lets the operator override per-call. Storage layer's `>=` predicate at `storage.py:1604` was already correct for any non-zero threshold, so no storage change was needed.
**Why:** Root-cause fix at the server boundary fixes every consumer in one diff (P16). Smaller blast radius than per-consumer cleanup. The CLI override preserves reversibility (P3) — any caller relying on 0.0 behavior can opt back in explicitly. Threshold of 0.1 chosen as the gap between observed noise (0.002–0.045) and observed real matches (0.2+); conservative enough to not drop genuine matches. Per task 2.1.

## Dual-service LLM configuration (light + deep)

**Context:** Deep extraction of high-priority content requires a more capable (and more expensive) model tier. A single `llm_service` field couldn't distinguish "summarize everything cheaply" from "deep-extract the high-priority items." Forcing one model for both paths meant either paying gpt-5-mini rates for trivial summaries or losing synthesis quality on the items that matter.
**Choice:** Config schema split into `llm_light_service` (required — routine summarization/evaluation/audio/context/api) and `llm_deep_service` (optional — HIGH-priority deep extraction). Added `auto_extract: "none" | "high" | "all"` threshold field. Light failure is fatal at startup; deep failure is warning-only with `unreachable` status and runtime deep extraction disabled.
**Why:** Cost discipline and quality discipline together. Operator opts into deep extraction explicitly; default is `auto_extract="none"` so no gpt-5-mini calls happen until configured. Graceful degradation means a deep-service outage doesn't take down the daemon — the cheap path keeps running. Per feature 1 iteration spec.

## Typer callback subcommand guard

**Context:** `@app.callback(invoke_without_command=True)` on `main()` ran `Config.from_file()` + `validate_llm_config()` before dispatching any subcommand. When the config was stale, the validator raised "run migrate-config" before typer could dispatch `migrate-config` — a self-referential error loop on exactly the configs migrate-config is designed to fix. Bug introduced by bb1caab (2026-04-07) and unnoticed until task 1.1's tightened detection surfaced it on the service→light_service migration path.
**Choice:** Add `ctx: typer.Context` parameter to `main()` and `if ctx.invoked_subcommand is not None: return` as the first statement. Default daemon run path (no subcommand) proceeds through daemon-lock + config-load + validator as before. Admin subcommands (migrate-config and any future additions) bypass the validation block entirely.
**Why:** Admin subcommands must run on the exact configs that make the daemon unable to start — that's their purpose. Typer-idiomatic pattern; no flag inversion, no config softening.

## Clean field rename (no backward-compat shim)

**Context:** Feature 1 required renaming `llm_service` to `llm_light_service`. Options: (a) retain `llm_service` as a computed property delegating to the new field, (b) retain both fields and deprecate the old one, (c) clean rename with exhaustive caller updates.
**Choice:** (c) Clean rename. `Config.llm_service` is absent post-task-1.1; any access raises `AttributeError`. Every call site in the daemon (9 files) updated in one diff. `Config.from_file()` detects stale configs with a single directional check (`if "light_service" not in llm: raise ValueError("run migrate-config")`) rather than branching on every legacy format.
**Why:** P2 (correctness) and P14 (smallest sufficient change) both pointed at the clean rename. A shim preserves a contract the operator explicitly revoked and adds code that must eventually be removed — two diffs instead of one. The grep gate (`! grep -rn "config\.llm_service" src/`) makes missed callers impossible to hide.

## Replace litellm with llm-core

**Context:** litellm v1.82.7/1.82.8 contained a credential stealer targeting API keys, SSH keys, and cloud credentials (supply chain compromise).
**Choice:** Replace all litellm usage with in-house llm-core package. Zero litellm references in codebase (INV-001).
**Why:** Supply chain security. llm-core provides the same functionality (complete, health_check, service routing, cost estimation) without the compromised dependency. Also decouples from litellm's sprawling dependency tree.

## Service-keyed circuit breaker

**Context:** Circuit breaker was a singleton — all consumers shared one breaker regardless of which LLM service they used.
**Choice:** get_circuit_breaker(service_name) returns per-service instances from a module-level registry.
**Why:** Multi-service future (different providers for different tasks). One service hitting quota shouldn't block others.

## llm-core service routing via services.toml

**Context:** Each consumer had hardcoded provider/model/api_key configuration.
**Choice:** Consumers receive a service_name string. llm-core resolves service → provider + model + credentials at call time via services.toml + apiconf.
**Why:** Single point of configuration. Changing providers means editing services.toml, not daemon code. Credentials managed by apiconf, not prismis config.

## migrate-config as idempotent command

**Context:** Existing users have config.toml with old [llm] format (provider/model/api_key fields).
**Choice:** `prismis-daemon migrate-config` command that creates services.toml, apiconf, pricing.toml, and updates config.toml. All writes guarded by existence checks.
**Why:** Safe to run multiple times. Doesn't overwrite user-customized files. Provides clear migration path without breaking existing installations.

## Config context fields use .get() with defaults

**Context:** Config.from_file() used bare dict access for [context] section fields. Users with configs predating the context auto-update feature crashed on startup.
**Choice:** Use .get() with sensible defaults for all context config fields.
**Why:** Backwards compatibility. Config migration shouldn't require updating every existing config file for optional features.
