# Dedup triage — clone-by-clone verdicts

Measured this run: `jscpd` (the pinned binary under
`/Users/rudy/.local/share/ember/checkout/shared/node_modules/.bin/jscpd`, v5.3.3), invoked exactly
as `findDuplicates` in `/Users/rudy/.local/share/ember/checkout/shared/verify/src/verify.ts` builds
it for this repo's three packages (`cli` python, `daemon` python, `tui` go):

```
jscpd /Users/rudy/development/projects/prismis \
  --format python,go \
  --ignore "**/test_*.py,**/*_test.py,**/tests/**,**/*_test.go" \
  --reporters json --output <tmp>
```

Result: **105 clones, 1,877 duplicated lines** — not the 106/1,889 the gate report named. The
missing one is `cli/src/cli/list.py` &harr; `cli/src/cli/search.py`: both files hand-write an
identical 8-line priority-color `if/elif/else` chain (see cluster 17), but that block is only
~29 tokens by jscpd's tokenizer, under its default 50-token minimum, so *this* run's jscpd does not
report it — every other reported block is &ge;6 lines and clears the token floor. I re-ran with both
`node` and `bun` as the interpreter (`Bun.spawnSync`'s own first argument) and got the same 105/1,877
both times, and confirmed `.claude/` (which holds several worktree checkouts with their own
`pyproject.toml`/`go.mod`) is gitignored and excluded by jscpd's default `.gitignore` respect. I
cannot explain the 1-clone/12-line gap beyond that; the duplication itself is real and I've included
it as cluster 17 on its own evidence, not the tool's.

Every cluster below groups jscpd's raw file-pairs by *root cause* (the thing actually repeated),
which sometimes merges several reported pairs into one helper decision and sometimes splits one
file's clones into two unrelated causes. The summary table's clone/line counts are re-summed from
the raw jscpd pairs in this run's `jscpd-report.json`, not copied from the gate's report.

## Summary table

| # | Cluster | Clones | Lines | Verdict | Helper | Home |
|---|---|---|---|---|---|---|
| 1 | tui API client request/response boilerplate | 24 | 413 | CONSOLIDATE | `doRequest(method, path, body, timeout) (int, []byte, error)` | `tui/internal/api/client.go` |
| 2a | storage.py: sources row&rarr;dict | 1 | 24 | CONSOLIDATE | `_source_row_to_dict(row)` | `daemon/src/prismis_daemon/storage.py` |
| 2b | storage.py: dict&rarr;ContentItem + insert (add_content/create_or_update_content) | 3 | 67 | CONSOLIDATE | `_content_item_from_dict(item)` | `daemon/src/prismis_daemon/storage.py` |
| 2c | storage.py: obs_log update-result boilerplate | 1 | 14 | CONSOLIDATE | `_log_update_result(op, row_count, duration_ms, error=None)` | `daemon/src/prismis_daemon/storage.py` |
| 2d | storage.py: content-row&rarr;dict (7 read methods) | 14 | 238 | CONSOLIDATE | `_content_row_to_dict(row)` | `daemon/src/prismis_daemon/storage.py` |
| 3 | cli API client httpx boilerplate | 16 | 308 | CONSOLIDATE | `_send`/`_send_json(method, path, ...)` | `cli/src/cli/api_client.py` |
| 4a | tui queries.go: row-scan/query-build duplication | 10 | 271 | CONSOLIDATE | `scanContentRows(rows *sql.Rows) ([]ContentItem, error)` | `tui/internal/db/queries.go` |
| 4b | tui queries.go: count-query boilerplate | 2 | 20 | CONSOLIDATE | `countQuery(query, errCtx string) (int, error)` | `tui/internal/db/queries.go` |
| 5a | source_modal.go: dead `renderList`/`renderAddForm`/`renderEditForm` duplicate the live `*ContentOnly` renderers | 7 | 92 | **DELETE** (dead code, not a merge) | n/a — remove the dead trio | `tui/internal/ui/source_modal.go` |
| 5b | source_modal.go: add/edit key-handler duplication in `Update()` | 2 | 30 | CONSOLIDATE | `handleFieldTab()` / `clearFormAndReturnToList()` | `tui/internal/ui/source_modal.go` |
| 6 | modal.go &harr; source_modal.go: `ViewWithOverlay` re-implemented to fix Go's non-virtual embedding dispatch | 2 | 21 | CONSOLIDATE | `overlayModal(bg, modalView string, ...) string` (takes the already-rendered view) | `tui/internal/ui/modal.go` |
| 7 | operations/sources.go: client-create + lookup boilerplate | 5 | 73 | CONSOLIDATE | `newClientOrErrMsg()`, `lookupOrErrMsg(id, client)` | `tui/internal/ui/operations/sources.go` |
| 8 | operations/context.go: high/medium/low topic formatting | 2 | 24 | CONSOLIDATE | `formatTopicSection(b *strings.Builder, heading string, topics []TopicSuggestion)` | `tui/internal/ui/operations/context.go` |
| 9 | operations/prune.go: count-only branch re-derives `GetPruneCount` | 2 | 21 | CONSOLIDATE (by delegation) | `HandlePruneCommand`'s CountOnly branch calls `GetPruneCount(msg.Days)()` | `tui/internal/ui/operations/prune.go` |
| 10a | __main__.py: pipeline component wiring (`run_scheduler` vs `--once`) | 2 | 40 | CONSOLIDATE | `build_orchestrator(config, storage) -> DaemonOrchestrator` | `daemon/src/prismis_daemon/__main__.py` |
| 10b | __main__.py: idempotent `[services.prismis-openai-deep]` append | 1 | 18 | CONSOLIDATE | `_append_deep_service_block(services_path, console)` | `daemon/src/prismis_daemon/__main__.py` |
| 11 | context_analyzer.py / evaluator.py / summarizer.py: LLM-call body | 5 | 96 | CONSOLIDATE (partial — see behavior risk) | `call_llm_with_circuit_breaker(service_name, system_prompt, user_prompt, action)` | new: `daemon/src/prismis_daemon/llm_call.py` |
| 12 | api.py: source-validate-with-timeout (`add_source`/`update_source`) | 1 | 15 | CONSOLIDATE | `async _validate_source_with_timeout(validator, url, type_)` | `daemon/src/prismis_daemon/api.py` |
| 13 | cli analyze.py &harr; daemon orchestrator.py: analysis-dict assembly | 1 | 9 | CONSOLIDATE (cross-stack, careful) | `build_llm_analysis(summary_result, evaluation, learned_preferences=None)` | `daemon/src/prismis_daemon/orchestrator.py` (or a new `daemon/src/prismis_daemon/analysis.py`) |
| 14 | commands/registry.go: age-filter arg parsing (`cmdUnprioritized`/`cmdPrune`/`cmdPruneForce`) | 2 | 16 | CONSOLIDATE | `parseAgeArg(cmdName string, args []string) (*int, *ErrorMsg)` | `tui/internal/commands/registry.go` |
| 15 | ui/helpers.go: `## Header` / `# Header` branches | 1 | 42 | CONSOLIDATE | `renderHeaderLine(headerText string, lines []string, i, width int, theme) ([]string, int)` | `tui/internal/ui/helpers.go` |
| 16 | ui/model.go: manual-refresh vs auto-refresh cursor-preserving command | 1 | 25 | CONSOLIDATE | `(m *Model) buildRefreshCmd(isAutoRefresh bool) tea.Cmd` | `tui/internal/ui/model.go` |
| 17 | cli list.py &harr; search.py: priority-color formatting (not in this run's jscpd output — see note above) | (0 per this run / 1 per the gate's) | 8 | CONSOLIDATE | `formatPriority(priority_val: str) -> str` | new: a small shared helper in `cli/src/cli/api_client.py` or a new `cli/src/cli/format.py` |

**Totals**: 18 named clusters, re-summing to 105 clones / 1,877 lines against this run's jscpd
output (cluster 17 is additional, found by reading, not by this run's tool output).
**17 CONSOLIDATE, 1 DELETE** (5a). Zero KEEP-SEPARATE: every cluster's duplication traces to one
real repeated fact, not incidental textual similarity — the closest thing to a "these must stay
apart" case is cluster 11, where one of the three copies (context_analyzer.py) is missing behavior
the other two have, so consolidating without a decision would either silently add or silently
remove circuit-breaker protection (see below).

---

## 1. tui API client: request/response boilerplate (`tui/internal/api/client.go`)

**Repeated fact**: every one of `AddSource`, `DeleteSource`, `UpdateSource`, `PauseSource`,
`ResumeSource`, `GetSources`, `UpdateContent`, `fetchEntriesWithParams`, `PruneCount`,
`PruneUnprioritized`, `GenerateAudioBriefing`, `ExtractEntry`, `GetContextSuggestions` (13 methods)
hand-writes: build `*http.Request`, set `X-API-Key` (and `Content-Type` when there's a body), call
`.Do(req)`, `defer resp.Body.Close()`, `io.ReadAll(resp.Body)` — read at
`tui/internal/api/client.go:199-247` (`AddSource`) through `:907-988` (`GetContextSuggestions`).

**Verdict**: CONSOLIDATE. Extract
`func (c *APIClient) doRequest(method, path string, body []byte, timeout time.Duration) (int, []byte, error)`
that builds, sends (via `c.httpClient`, or a one-off `&http.Client{Timeout: timeout}` when
`timeout > 0`), and reads the body — returning the raw status code and bytes. Every caller keeps its
own status-code branching and JSON unmarshal exactly as today; only the mechanical part moves.

**Behavior risk** (verified by reading every method body, `client.go:199-988`):
- `PauseSource` (`:345-380`), `ResumeSource` (`:383-418`), `PruneCount` (`:626-674`),
  `PruneUnprioritized` (`:677-725`) never branch on `resp.StatusCode` at all — they parse the body
  and check only `apiResp.Success`. The other nine methods check `resp.StatusCode` first. A shared
  helper must not add status-code checking to these four; `doRequest` returning the raw status and
  letting each caller decide keeps that intact.
- `GenerateAudioBriefing` (`:738-823`), `ExtractEntry` (`:828-889`), `GetContextSuggestions`
  (`:907-988`) each construct a **fresh** `&http.Client{Timeout: N}` instead of using `c.httpClient`
  — they lose `c.httpClient`'s custom `Transport` (connection pooling, `DialContext`/
  `TLSHandshakeTimeout`/`IdleConnTimeout` tuning built in `NewClientWithURL`, `:179-196`). `doRequest`'s
  `timeout` parameter must preserve this (a one-off client on override, not a timeout set on
  `c.httpClient`'s existing transport) so the pooling loss is unchanged, not "fixed" as a side effect.
- Distinct HTTP status codes get distinct messages per caller: 404 means "source not found" in
  `DeleteSource`/`UpdateSource` but "content not found" in `UpdateContent`; `ExtractEntry` has a
  unique 503 branch reading `apiResp.Data["reason"]` (`:855-871`). None of this moves into the helper.

**Tests**: `tui/internal/api/client_test.go` covers `AddSource`, `DeleteSource`, `GetSources` plus
cross-cutting cases (`TestMalformedJSONHandling`, `TestDaemonUnavailable`, `TestInvalidAPIKeyNoLeak`,
`TestNetworkTimeoutRecovery`) via `net/http/httptest` — a real HTTP server, not a mock of prismis's
own code, consistent with the constitution's LLM-only mocking rule. **Gap**: no direct test for
`UpdateSource`, `PauseSource`, `ResumeSource`, `UpdateContent`, `FetchEntries`/`FetchEntriesSince`,
`PruneCount`, `PruneUnprioritized` — the four status-code-skipping methods above are exactly the ones
with no test, so nothing today would catch `doRequest` accidentally adding status-code gating to
them. Before consolidating, add an `httptest.Server`-backed test per untested method asserting method/
path/headers and the no-status-check behavior for the four that skip it.

---

## 2. daemon storage.py (four sub-causes, one file)

### 2a. Sources row&rarr;dict (`get_active_sources` / `get_all_sources`)

**Repeated fact**: `get_active_sources` (`storage.py:136-173`, `WHERE active = 1 ORDER BY id`) and
`get_all_sources` (`:919-955`, no WHERE, `ORDER BY created_at DESC`) run the same 10-column
`SELECT` and build the identical 10-key dict per row.

**Verdict**: CONSOLIDATE — `_source_row_to_dict(row) -> dict[str, Any]`, called from both.
**Behavior risk**: none found — the two dict-construction blocks are byte-identical.

### 2b. dict&rarr;ContentItem coercion + insert (`add_content` / `create_or_update_content`)

**Repeated fact**: `create_or_update_content`'s own docstring/comments say "same logic as
add_content" (`storage.py:330`, `:345`) — the dict-to-`ContentItem` coercion block
(`:193-231` in `add_content`, `:346-384` in `create_or_update_content`) and the
analysis-serialize-then-`INSERT INTO content` block (`:254-298` vs `:424-459`) are copy-pasted on
purpose, by the author's own admission.

**Verdict**: CONSOLIDATE — `_content_item_from_dict(item: dict) -> ContentItem` for the coercion
(both call `self.get_active_sources()` for the source_id default, so it's a clean instance method).

**Behavior risk — real, must be preserved, not merged away**: `add_content` inserts through
`conn = get_db_connection(self.db_path)` (`:233`, a **fresh** connection per call), while
`create_or_update_content`'s insert branch uses `self.conn` (`:398`, `:441` — the instance's shared
connection). The extracted coercion helper must stay pure (no DB write inside it) so this connection
choice stays with each caller; do not also merge the INSERT statement into a helper that takes a
connection unless that parameter is threaded through explicitly and tested against both connection
modes.

### 2c. obs_log update-result boilerplate (`update_content_status` / `flag_interesting`)

**Repeated fact**: both wrap a single-row `UPDATE` in `self.conn.execute` → `self.conn.commit()` →
`obs_log("db.update", ..., status="success" if row_count > 0 else "not_found")`, with the same
try/except/rollback/`obs_log(..., status="error")` shape (`:1123-1151` vs `:1166-1195`).

**Verdict**: CONSOLIDATE — `_log_update_result(operation: str, row_count: int, duration_ms: int,
error: str | None = None) -> None` wrapping the `obs_log` calls only (the SQL stays per-caller).
**Behavior risk**: none — the logged fields and status logic are identical.

### 2d. content-row&rarr;dict, 7 read methods

**Repeated fact**: `get_content_by_priority` (`:647-681`), `get_content_since` (`:730-764`),
`get_content_by_id` (`:1263-1315`), `get_latest_content_for_source` (`:1317-1370`), the semantic-search
result builder inside `search_content` (`:1637-1657`), `get_content_without_embeddings`
(`:1738-1781`), `get_content_without_analysis` (`:1827-1873`), and `get_content_needing_kind`
(`:1898-1949`) each hand-write the same `content JOIN sources` row&rarr;dict mapping. All eight
queries `SELECT c.*, s.name as source_name, s.type as source_type ... LEFT/JOIN sources`, so every
row genuinely carries the full column set regardless of which subset each hand-written dict picks.

**Verdict**: CONSOLIDATE — one `_content_row_to_dict(row: sqlite3.Row) -> dict[str, Any]` returning
the full canonical field set (union of every field ever extracted: id, source_id, external_id,
title, url, content, summary, `analysis` via the existing `self._parse_analysis_json`, priority,
published_at, fetched_at, read, favorited, interesting_override, user_feedback, notes, source_name,
source_type, created_at, updated_at), used by all eight. `_get_by_external_id` (`:546-599`) queries
plain `content` with no join, so its narrower 13-field dict cannot use this helper as-is — either
keep it as its own small helper or add the same `LEFT JOIN sources` to that one query (a separate,
larger decision, out of scope here — flag it, don't fold it in silently).

**Behavior risk — a likely latent bug, found by comparing the field lists directly**:
`get_latest_content_for_source` (`:1317-1370`) omits `user_feedback` from its returned dict — every
other joined-row method that has both `interesting_override` and `user_feedback` in its `SELECT`
includes both in the dict (`get_content_by_id`, `get_content_by_priority`, `get_content_since`
all include it; `storage.py:1346-1366` is the one place it's silently dropped even though the row
has the column). A caller reading a source's latest item and checking `user_feedback` gets a
`KeyError`/missing key today; a naive merge to the canonical mapper would silently start returning it
— which is very likely the *fix*, not a regression, but it must be called out as a behavior change,
not folded in as if it were pure refactor. Separately, several call sites bypass the codebase's own
existing `_parse_analysis_json` helper (`storage.py:76`) and inline `json.loads(row["analysis"]) if
row["analysis"] else None` instead (`_get_by_external_id:584-586`, `get_content_by_priority:653-654`,
`get_content_since:736-737`) — routing everything through the canonical mapper fixes this
inconsistency for free.

**Tests**: `daemon/tests/integration/test_file_fetcher_integration.py` has
`test_get_latest_content_for_source_returns_actual_latest`,
`test_get_latest_content_for_source_with_multiple_sources`,
`test_get_latest_content_for_source_with_no_previous_entry` — all real-DB tests (`test_db: Path`
fixture, per the constitution's no-internal-mocking rule) — but none assert on `user_feedback`
(`grep user_feedback` on that file: no hits), so none of them would catch the field being added or
staying missing. **Gap**: add an assertion on `user_feedback` presence/value to at least one of
those three before consolidating, so the behavior change (if made) is proven, not assumed.

---

## 3. cli API client httpx boilerplate (`cli/src/cli/api_client.py`)

**Repeated fact**: 13 of `APIClient`'s methods (`add_source`, `remove_source`, `pause_source`,
`resume_source`, `count_unprioritized`, `prune_unprioritized`, `get_report`, `edit_source`,
`get_entry`, `get_content`, `get_archive_status`, `search`, `get_statistics`, `get_sources`,
`extract_entry` — read in full, `api_client.py:69-758`) share:
`with httpx.Client(timeout=...) as client: try: response = client.<verb>(url, headers={"X-API-Key":
self.api_key}, ...); data = response.json(); if response.status_code >= 400: raise
RuntimeError(data.get("message", ...)); if not data.get("success"): raise RuntimeError(...); return
<projection of data>; except httpx.RequestError as e: raise RuntimeError(f"Network error: {e}") from
e; except Exception as e: ... raise RuntimeError(f"Unexpected error: {e}") from e`.

**Verdict**: CONSOLIDATE — `_send(method, path, *, json=None, params=None, timeout=None) ->
httpx.Response` (builds/sends/wraps `httpx.RequestError`/unexpected exceptions into `RuntimeError`)
and `_send_json(method, path, **kw) -> dict` (calls `_send`, does the standard status/success check,
returns the parsed body). Each caller keeps its own `.get("data", {})...` projection.

**Behavior risk**: `get_entry_raw` (`:429-461`) is the one outlier — it does **not** call
`response.json()` at all, doesn't check `data.get("success")`, and returns `response.text` with a
different generic error message ("Entry not found or API error: {status}"). It must use `_send`
only (the low-level primitive), never `_send_json`. `extract_entry` (`:713-757`) overrides the
30s class default with `httpx.Timeout(120.0)` — the `timeout` parameter on `_send`/`_send_json`
must be threaded through, not silently defaulted to `self.timeout`.

**Tests**: `cli/tests/unit/test_api_client_search_params.py` (search's `min_score` param),
`test_list_kind_filter_unit.py` (`get_content`'s `kind` param), `test_extract_entry_unit.py`
(`extract_entry`'s 120s timeout, HTTP-error and network-error paths, against `live_daemon`/real
httpx round trips). **Gap**: `add_source`, `remove_source`, `pause_source`, `resume_source`,
`count_unprioritized`, `prune_unprioritized`, `get_report`, `edit_source`, `get_entry`,
`get_entry_raw`, `get_archive_status`, `get_statistics`, `get_sources` — 13 of 16 methods — have no
direct httpx-level test. Add at least one real-HTTP-round-trip test per untested method (respx or a
local ASGI test app, matching the existing `param_probe`-style fixtures) before consolidating.

---

## 4. tui db/queries.go (two sub-causes)

### 4a. Row-scan/query-build duplication

**Repeated fact**: `queryContentWithFilter` (`queries.go:38-154`), `GetContentWithFilters`
(`:180-334`), `GetAllContent` (`:339-435`), and the unprioritized-content query (whose row-scan loop
is the `270-321`/`597-648` pair) each build their own `SELECT c.id, ... FROM content c JOIN sources s
...` string, then repeat the exact same 15-column `rows.Scan(...)` into a fresh `ContentItem`, the
same eight `if X.Valid { item.X = X.String }` nullable-field copies, and the same
`time.Parse(time.RFC3339, ...)` for `Published` (compare `:83-154` to `:253-334`, byte-identical).

**Verdict**: CONSOLIDATE — `scanContentRows(rows *sql.Rows) ([]ContentItem, error)` doing the
`for rows.Next() {...}` loop plus `rows.Err()` check; each function keeps building its own query
string/args and just calls `db.Query(...)` then `scanContentRows(rows)`.

**Behavior risk**: none found — the column list and order are identical at every site I read
(`:47-48`, `:188-189`, `:346-347`, `:560-561`), so this is a mechanical, safe extraction.

### 4b. Count-query boilerplate

**Repeated fact**: `getUnprioritizedCount` (`:655-673`), `GetArchivedCount` (`:676-693`),
`GetFavoritesCount` (`:696-...`) each do `db, err := GetDB(); ...; var count int; err =
db.QueryRow(<SQL>).Scan(&count); if err != nil { return 0, fmt.Errorf("failed to count %s: %w", ...) };
return count, nil`.

**Verdict**: CONSOLIDATE — `countQuery(query, errContext string) (int, error)`.
**Behavior risk**: none — only the SQL text and error-message noun differ.

**Tests**: `tui/internal/db/queries_test.go` — `TestGetContentByPriority`,
`TestGetContentByPriorityEmptyDB`, `TestGetUnreadContent`, `TestSourceInfoPopulated` (specifically
exercises field-by-field row population), `TestGetAllContent_ReturnsAllNonArchived`,
`TestGetAllContent_ArchivedFilter`, `TestGetFavoritesCount_ReturnsAccurateTotal`,
`TestZGetFavoritesCount_HandlesDBError` — all real sqlite DB tests. **Gap**: `GetContentWithFilters`
and the unexported unprioritized-content query function have no test calling them directly (only
indirectly, if at all, through higher-level TUI flows) — check before routing them through
`scanContentRows`.

---

## 5. source_modal.go — two unrelated causes in one file

### 5a. Dead `renderList`/`renderAddForm`/`renderEditForm` duplicate the live `*ContentOnly` renderers — **DELETE, not consolidate**

This is the one cluster in this triage that is not really "two copies of the same logic" — it's one
live implementation and one **unreachable** implementation that happens to look like a copy because
it was never deleted after a refactor. Traced by reading the actual call graph, not assumed:

- `SourceModal.View(theme)` (`source_modal.go:550-660`) is the **only** renderer ever displayed: for
  list mode it writes `m.viewport.View()` (`:600`, populated by `renderListContentOnly()` via
  `UpdateContent()`, `:378`); for add/edit/confirm modes it calls `renderAddContentOnly()` (`:607`),
  `renderEditContentOnly()` (`:609`), `renderConfirmContentOnly()` (`:611`) directly. It never reads
  `m.content` (the embedded `Modal.content` field).
- `UpdateContent()` (`:374-387`) *also* calls `m.SetContent(m.renderList())` / `m.SetContent(m.
  renderAddForm())` / `m.SetContent(m.renderEditForm())` for list/add/edit modes — writing into
  `Modal.content` via `Modal.SetContent` (`modal.go:43-46`).
- The only reader of `Modal.content` is `Modal.View` (`modal.go:46-77`) — but `SourceModal` overrides
  both `View` (`source_modal.go:550`) **and** `ViewWithOverlay` (`:835-891`, which calls `m.View(theme)`
  at `:841` where `m` is statically `SourceModal`, so Go resolves the override, not the embedded
  `Modal.View`). I confirmed no code path calls `m.sourceModal.Modal.View(...)` or otherwise reaches
  the base `Modal.View` for the source modal (`grep -rn "sourceModal\.Modal\.\|sourceModal\.View("
  tui/` — no matches).

  So `renderList()` (`:390-472`), `renderAddForm()` (`:475-509`), `renderEditForm()` (`:512-547`), and
  the four `m.SetContent(...)` calls in `UpdateContent()` that feed them, compute output nothing ever
  displays. That is the actual root cause of 7 of the file's 9 self-clones (all pairs except the two
  in `Update()`'s key handling, cluster 5b): `renderList` duplicates `renderListContentOnly`'s source-
  list loop and error block (`:406-427`&harr;`:667-688`, `:434-439`&harr;`:699-704`,
  `:457-472`&harr;`:741-756`); `renderAddForm` duplicates `renderAddContentOnly`
  (`:483-495`&harr;`:763-775`, `:500-509`&harr;`:775-784`); `renderAddForm`/`renderEditForm` even
  duplicate *each other* (`:495-509`&harr;`:533-547`, both dead); `renderAddForm` duplicates
  `renderEditContentOnly` too (`:500-509`&harr;`:804-813`).

**Verdict**: DELETE `renderList`, `renderAddForm`, `renderEditForm`, and the three `m.SetContent(...)`
calls in `UpdateContent()` for list/add/edit modes (keep `m.viewport.SetContent(m.
renderListContentOnly())` — that one is live). Removal eliminates 7 of the 9 reported clones by
removing one whole side of each pair, which is the correct "done right" fix the task asked for —
not a merge of two live behaviors.

**Behavior risk — this is also a test-honesty finding, not just dead code**:
`tui/internal/ui/source_modal_test.go` has two tests that assert directly on the dead field:
`modal.content` (not what a user sees) in the "No sources configured" case and in
`TestSourceModal_ErrorMessageDisplay` (`source_modal_test.go:90-95`, `:107-111`, calling
`modal.UpdateContent()` then checking `strings.Contains(modal.content, ...)`). These tests currently
**pass by checking dead code's output** — they give false confidence that the source modal's error
message displays correctly; they would not catch a regression in what's actually rendered
(`m.viewport`/`renderListContentOnly`). Deleting the dead functions must come with rewriting these
two tests to assert against `modal.viewport.View()` (or a public accessor) instead of `modal.content`,
in the same change — otherwise the delete breaks compilation and the honest fix gets reverted or
patched around instead of done.

### 5b. add/edit key-handler duplication in `Update()`

**Repeated fact**: the `"add"` and `"edit"` cases of `SourceModal.Update`'s mode switch
(`source_modal.go:190-229` and `:231-306`) share identical tab-switching (`:194-203`&harr;`:235-244`)
and identical esc-handling + textinput-passthrough (`:212-... ` region, matched at
`:212-231`&harr;`:289-308`) — both live, both actually reachable.

**Verdict**: CONSOLIDATE — small helpers `(m *SourceModal) handleFieldTab()` (the URL/name Tab swap)
and a shared esc/clear-form helper, called from both branches.
**Behavior risk**: none found in what I read — the two blocks are byte-identical.

---

## 6. modal.go &harr; source_modal.go: duplicated `ViewWithOverlay`

**Repeated fact**: `Modal.ViewWithOverlay` (`modal.go:81-138`) and `SourceModal.ViewWithOverlay`
(`source_modal.go:835-891`) are ~55-line near-duplicates: same background-dimming loop, same
centering math, same line-splicing. `SourceModal` had to fully re-declare it purely because Go's
embedding has no virtual dispatch — `Modal.ViewWithOverlay` calling `m.View(theme)` always resolves
to `Modal.View` when invoked through the embedded value, never the override, so the *only* way to
make the overlay call `SourceModal.View` is to re-implement the whole compositing function around it
(this is why the override exists at all, not an oversight).

**Verdict**: CONSOLIDATE the compositing math, not the dispatch problem — extract a free function
`overlayModal(backgroundView, modalView string, termWidth, termHeight, width int) string` that takes
the **already-rendered** modal view as a parameter (so each type still supplies its own `m.View(theme)`
call, one line, outside the shared function). Both `Modal.ViewWithOverlay` and `SourceModal.
ViewWithOverlay` become a one-line call to `overlayModal(bg, m.View(theme), ...)`.

**Behavior risk — two numeric differences that look like drift, not intent**:
`Modal.ViewWithOverlay` uses `modalWidth := m.width + 4` (`modal.go:112`) and
`startY := modalMax(0, ...)` (`:115`); `SourceModal.ViewWithOverlay` uses `modalWidth := m.width`
(no `+4`, `source_modal.go:866`) and `startY := modalMax(1, ...)` (`:869`, with a comment "Start at
least at line 1 to not overlap header"). The `+4` accounts for `Modal`'s own border+padding
(`Border(...).Padding(1, 2)`, `modal.go:56-58`) — `SourceModal`'s own `View()` doesn't add that
uniform border (it builds its own layout), so the two width formulas are probably each correct for
their own content, not a bug — but a shared `overlayModal` must take `modalWidth` and `startY`'s
floor as parameters (or derive `modalWidth` from `lipgloss.Width(modalView)` instead of a stored
field), not silently pick one type's constant for both.

**Tests**: no test file references `ViewWithOverlay` directly by name in either `modal.go`'s or
`source_modal.go`'s test files (checked `source_modal_test.go`); this is rendering-composition code
exercised only indirectly through whatever golden/snapshot tests exist for the full TUI view, if any.
Flag as untested either way — extracting `overlayModal` should come with at least one direct test of
its centering math on a synthetic background+modal pair.

---

## 7. operations/sources.go: client-create + source-lookup boilerplate

**Repeated fact**: `RemoveSource`, `PauseSource`, `ResumeSource`, `EditSourceName` (and `AddSource`,
for the client-create half only) each start with the same `apiClient, err := api.NewClient(); if err
!= nil { return SourceOperationMsg{Message: fmt.Sprintf("Failed to create API client: %v", err),
Success: false, Error: err} }`, and `RemoveSource`/`PauseSource`/`ResumeSource`/`EditSourceName` all
follow it with the same `sourceID, sourceName, err := lookupSourceByIdentifier(identifier,
apiClient); if err != nil { return SourceOperationMsg{Message: err.Error(), ...} }`
(`operations/sources.go:95-114`, `:135-154`, `:175-194`, `:215-234` — read in full, `:1-274`).

**Verdict**: CONSOLIDATE — `newClientOrErrMsg() (*api.APIClient, tea.Msg)` and
`lookupOrErrMsg(identifier string, apiClient *api.APIClient) (id, name string, errMsg tea.Msg)`,
each caller doing `if errMsg != nil { return errMsg }` then continuing with its own operation-
specific API call and its own success message/emoji (`"✓ Removed source: %s"`, `"⏸ Paused source:
%s"`, `"▶ Resumed source: %s"` all differ and stay put).

**Behavior risk**: none found — the error-wrapping shape is identical across all four/five sites.

**Tests**: no test file exists under `tui/internal/ui/operations/` at all (`find
tui/internal/ui/operations -iname "*_test.go"` — empty). This whole package's dedup work has zero
direct test coverage today; add at least a real-HTTP-backed test (via a fake daemon `httptest.Server`)
per operation before or alongside the consolidation, not after.

---

## 8. operations/context.go: high/medium/low topic formatting

**Repeated fact**: the high/medium/low branches (`context.go:101-113`, `:115-127`, `:129-141`) format
each `TopicSuggestion` identically — only the heading text and the source slice differ.

**Verdict**: CONSOLIDATE — `formatTopicSection(formatted *strings.Builder, heading string, topics
[]TopicSuggestion)`, called three times.
**Behavior risk**: none — bodies are byte-identical.
**Tests**: none found under `operations/` (see cluster 7's note — the whole package is untested).

---

## 9. operations/prune.go: `HandlePruneCommand`'s CountOnly branch re-derives `GetPruneCount`

**Repeated fact**: `GetPruneCount` (`prune.go:27-50`) and the `msg.CountOnly` branch of
`HandlePruneCommand` (`:85-108`) both create a client, call `apiClient.PruneCount(days)`, and return
a `PruneCountMsg` — differing only in `ShowOnly: true` and reading `msg.Days` vs a `days` parameter.

**Verdict**: CONSOLIDATE by **delegation**, not a new helper — `HandlePruneCommand`'s CountOnly branch
should call `GetPruneCount(msg.Days)()` and, on a `PruneCountMsg` result, set `ShowOnly = true` before
returning it (passing any `PruneResultMsg` error through unchanged). This removes the duplicate body
entirely rather than factoring out a third function.
**Behavior risk**: none — `GetPruneCount`'s error and success shapes are exactly what the CountOnly
branch already produces.
**Tests**: none found under `operations/` (same gap as clusters 7-8).

---

## 10. daemon __main__.py (two sub-causes)

### 10a. Pipeline component wiring (`run_scheduler` vs `--once` mode)

**Repeated fact**: `run_scheduler` (`__main__.py:141-189`) and the `--once` branch of `main`
(`:432-481`) both build `Storage()`, all four fetchers, `notification_config`, `ContentSummarizer`,
`ContentEvaluator`, `Notifier`, the optional `deep_extractor`/`kind_classifier`, and a
`DaemonOrchestrator` from the same fields, in the same order, then diverge only after — one calls
`build_scheduler(...)`, the other calls `orchestrator.run_once()`.

**Verdict**: CONSOLIDATE — `build_orchestrator(config: Config, storage: Storage) -> DaemonOrchestrator`
(or a small dataclass bundling storage+orchestrator if callers need `storage` separately, which
`run_scheduler` does for `build_scheduler`), used by both.
**Behavior risk**: none found — both blocks read the same config fields in the same order.

### 10b. Idempotent `[services.prismis-openai-deep]` append

**Repeated fact**: inside `migrate_config`, the "service&rarr;light_service rename" branch
(`:551-572`) and the "pre-llm-core install" branch (`:687-703`) both append the same
`[services.prismis-openai-deep]` TOML block to `services.toml`, guarded by the same
`if "[services.prismis-openai-deep]" in services_text` check. A comment at `:682-686` explicitly
says this mirrors the other branch's pattern "so a single run of migrate-config on a pre-llm-core
config converges to the full dual-service shape."

**Verdict**: CONSOLIDATE — `_append_deep_service_block(services_path: Path, console) -> None`,
called from both branches. The author's own comment already frames this as one behavior duplicated
on purpose for convergence, not two independent decisions, so merging it is not a design change.
**Behavior risk**: none — the appended text and the idempotency check are byte-identical.

**Tests**: `daemon/tests/unit/test_llm_core_migration_unit.py`'s
`test_SC15_migrate_config_creates_services_and_updates_config` and
`test_SC15_migrate_config_is_idempotent` both drive `migrate_config()` with `_OLD_FORMAT_CONFIG_TOML`
— the "pre-llm-core install" branch only. **Gap**: neither test exercises the "service&rarr;
light_service rename" branch (the other copy of the append block, `:551-572`); add a fixture for an
already-migrated `[llm] service=` config before consolidating, so both call sites of the extracted
helper are proven.

---

## 11. context_analyzer.py / evaluator.py / summarizer.py: LLM-call body

**Repeated fact**: `context_analyzer.py._call_llm` (`:251-330`), `evaluator.py._call_llm`
(`:166-266`), and the inline LLM-call block in `summarizer.py` (`:~90-169`) all: extract
system/user prompt from `messages`, call `complete(prompt=..., system_prompt=..., service=...,
json=True)`, extract `tokens`/`cost_usd`, `obs_log("llm.call", ..., status="success")` on success,
`obs_log(..., status="error")` + re-raise on failure, then `extract_json(response_text)` and raise
`ValueError` if parsing fails.

**Verdict**: CONSOLIDATE **only the genuinely identical part** — a shared
`call_llm_with_circuit_breaker(service_name: str, system_prompt: str, user_prompt: str, action: str)`
covering the circuit-breaker check, the `complete()` call, token/cost extraction, and the
`obs_log`/circuit-breaker success-or-failure recording. Leave JSON-parsing and error handling (raise
vs. return `None`) with each caller — see behavior risk below for why.

**Behavior risk — the most consequential finding in this triage; do not merge blindly**:
- `evaluator.py._call_llm` (`:187-200`) and `summarizer.py` (`:~90-109`) both check
  `get_circuit_breaker(self.service_name).check_can_proceed()` **before** calling `complete()`,
  raising `RuntimeError` if the circuit is open, and both call `circuit.record_success()` /
  `circuit.record_failure(e)` around the call (`evaluator.py:233`, `:237`; matching lines in
  `summarizer.py:142`, `:146`).
- `context_analyzer.py._call_llm` (`:251-330`) has **no circuit-breaker check and no
  record_success/record_failure calls at all** — it calls `complete()` unconditionally. I read the
  whole method; this is not present anywhere in it.
- A naive "pick the fuller implementation" merge would silently add circuit-breaker gating to
  context analysis (possibly the right fix — context analysis calls the same LLM boundary as
  everything else and arguably should respect the same quota protection) or, if someone merges the
  other direction, silently strip circuit-breaker protection from the two callers that run on every
  fetched item. Either direction is a behavior change that needs an explicit decision, not an
  accidental one made by which file happened to be the "keep" side of the diff.
- `summarizer.py`'s version additionally differs in error contract: on `extract_json` failure it
  `return None` (`:164-169`, and the required-field checks after it also `return None`), while
  `context_analyzer.py`/`evaluator.py` `raise ValueError`. This is why the parse/error-handling half
  must **not** move into the shared helper — only the pre-parse LLM-call mechanics should.

**Tests**: `daemon/tests/unit/test_circuit_breaker_unit.py` tests the circuit breaker class in
isolation. `daemon/tests/unit/test_evaluator_unit.py` (136 lines, read in full via grep) has **zero**
references to "circuit" — no test proves evaluator's circuit-breaker branch. `daemon/tests/
integration/test_context_assistant.py` (context_analyzer's test file) also has zero "circuit"
references — consistent with the gap being real, not just untested. **This is the load-bearing gap
for cluster 11**: before extracting `call_llm_with_circuit_breaker`, add a test per caller (real
`complete()` faked at the LLM boundary only, per the constitution) that proves circuit-open behavior
for evaluator/summarizer and makes an explicit, visible decision — assert-and-add for
context_analyzer, or assert-and-confirm-intentional-absence — rather than let the extraction settle
the question implicitly.

---

## 12. daemon api.py: source-validate-with-timeout

**Repeated fact**: `add_source` (`api.py:~480-529`) and `update_source`'s URL-change branch
(`:582-...`, read through `:619`) both run
`await asyncio.wait_for(asyncio.to_thread(validator.validate_source, normalized_url, request.type),
timeout=SOURCE_VALIDATION_TIMEOUT)`, catch `TimeoutError` and raise `ValidationError` with the same
message, and raise `ValidationError(f"Source validation failed: {error_msg}")` when `is_valid` is
`False`.

**Verdict**: CONSOLIDATE — `async def _validate_source_with_timeout(validator, url: str, type_: str)
-> dict | None` (returns `metadata`, raises `ValidationError` on timeout/failure), called from both
handlers.
**Behavior risk**: none found — both blocks use the identical timeout constant and identical
messages.

**Tests**: `daemon/tests/integration/test_source_add_normalization_integration.py` hits `POST
/api/sources` through a real test client against `test_db`, exercising `add_source`'s validation
path for URL-normalization scenarios. **Gap**: no equivalent test found for `update_source`'s
validation branch specifically (its own timeout/failure path) — add one before consolidating so both
call sites of the extracted helper are proven, not just one.

---

## 13. cli analyze.py &harr; daemon orchestrator.py: analysis-dict assembly (cross-stack)

**Repeated fact**: `cli/src/cli/analyze.py` (`:174-185`) and `daemon/src/prismis_daemon/
orchestrator.py` (`:285-297`) both build an analysis dict from a `summary_result` and an
`evaluation`: `reading_summary`, `alpha_insights`, `patterns`, `quotes`, `tools`, `urls`,
`matched_interests` (from `evaluation`), `priority_reasoning` (from `evaluation.reasoning`),
`metadata` — the same seven `summary_result.*` fields plus the same two `evaluation.*` fields, same
keys, same order.

**Verdict**: CONSOLIDATE, but the shared function's home follows the dependency direction: `cli`
already depends on `daemon` optionally (`cli/pyproject.toml:14-16`, the `[local]` extra: `local =
["prismis-daemon"]`), and `cli/analyze.py` already does lazy `from prismis_daemon.X import Y` inside
its functions for `Storage`, `Config`, `ContentEvaluator`, `ContentSummarizer`, `KindClassifier`,
`obs_log` (`analyze.py:37`, `:93`, `:111-114`, `:270`, `:283-285`) — this cluster is one more instance
of that same existing pattern, not a new coupling. The shared function belongs in `daemon` (e.g.
`prismis_daemon.orchestrator.build_llm_analysis` or a smaller new `prismis_daemon/analysis.py` if
pulling in `orchestrator.py`'s own dependencies into a lazy CLI import is undesirable), and
`cli/analyze.py` imports it the same lazy way it imports everything else from `prismis_daemon`. The
daemon must **never** import from `cli` (constitution Principle V) — this consolidation only works
in the cli&rarr;daemon direction, which is what already exists.

**Behavior risk — real divergence, must be a parameter, not silently added or dropped**:
- `orchestrator.py`'s dict includes `"preference_influenced": evaluation.preference_influenced`
  (`:295`); `cli/analyze.py`'s dict (`:175-185`) does **not** have this key at all.
- `orchestrator.py` calls `self.evaluator.evaluate_content(..., learned_preferences=
  learned_preferences)` (`:277-283`); `cli/analyze.py`'s call to `evaluator.evaluate_content(...)`
  (`:167-172`) omits `learned_preferences` entirely — the CLI's local/offline `analyze` command has
  no concept of learned preferences today.
- `orchestrator.py` merges existing analysis via a dedicated `self._merge_analysis(existing_analysis,
  llm_analysis)` (`:299-303`); `cli/analyze.py` inlines a narrower merge that only preserves
  `metrics` (`:187-190`).

  The shared `build_llm_analysis(summary_result, evaluation, learned_preferences=None)` must accept
  `preference_influenced`/`learned_preferences` as optional and omit the key when not supplied
  (matching cli's current output exactly) rather than always including it (which would silently
  change cli's analysis shape) or always omitting it (which would regress the daemon's pipeline).
  The merge-with-existing-analysis step should **not** be folded into this helper at all — it stays
  a separate decision per caller given the width difference already described.

**Tests**: `cli/tests/unit/test_analyze_kinds_unit.py` exists for `analyze.py`'s kind-related
behavior; I did not find a test asserting on `cli/analyze.py`'s analysis-dict shape (i.e., that it
omits `preference_influenced`) — add one before extracting the shared helper, so the omission is
pinned as an assertion, not just current behavior that a refactor could silently change.

---

## 14. commands/registry.go: age-filter arg parsing

**Repeated fact**: `cmdUnprioritized` (`registry.go:174-191`), `cmdPrune` (`:194-212`),
`cmdPruneForce` (`:215-...`) each parse an optional `args[0]` via `parseAge`, returning an
`ErrorMsg` with a command-specific prefix (`"unprioritized: invalid age filter..."`, `"prune: ..."`,
`"prune!: ..."`) on a negative result.

**Verdict**: CONSOLIDATE — `parseAgeArg(cmdName string, args []string) (*int, *ErrorMsg)`, called by
all three, with `cmdName` supplying the message prefix.
**Behavior risk**: none — only the prefix string differs.
**Tests**: no test file matches "prune" or "age" under `tui/internal/commands/*_test.go` — add a
table-driven test over `parseAgeArg`'s error-prefix behavior before consolidating, since three call
sites collapsing into one function with no test is exactly how a prefix typo would go unnoticed.

---

## 15. ui/helpers.go: `## Header` / `# Header` branches

**Repeated fact**: the `## ` and `# ` header-handling branches of the markdown-to-TUI renderer
(`helpers.go:77-122` and `:123-168`) are byte-identical bodies — both special-case an "Overview"
header into a bordered box (collecting lines until the next header/bullet/blank, wrapping, boxing),
and otherwise render `"▸ " + headerText` in bold cyan.

**Verdict**: CONSOLIDATE — `renderHeaderLine(headerText string, lines []string, i, width int, theme)
([]string, int)` (returns the lines to append and how many source lines were consumed for the
Overview-collection case), called from both branches with `headerText` computed from the
`"## "`/`"# "` prefix each already strips.
**Behavior risk**: none — verified byte-for-byte identical in the two ranges read.
**Tests**: no `helpers_test.go` exists at all; `reader_test.go`'s visible tests
(`TestReaderView`, `TestParseMetadata_DeepExtraction`, `TestAppendSynthesisSection`,
`TestUnifiedQuotes`, etc.) don't obviously exercise the "## Header"/"Overview" special-casing by
name. Treat this as untested and add a direct test of `renderHeaderLine`'s Overview-boxing behavior
before or alongside the extraction.

---

## 16. ui/model.go: manual-refresh vs auto-refresh cursor-preserving command

**Repeated fact**: two closures (`model.go:273-303` and `:948-979`) both capture `currentItemID`,
set `m.loading = true`, and build an `itemsLoadedMsg` via the same remote/local
`db.GetAllContent`+`db.GetDistinctKinds`+`applyFiltersClientSide`+`countHiddenUnprioritized` logic,
then set `result.preserveCursor = true` and `result.targetItemID = currentItemID` — differing only
in that the second also sets `result.isAutoRefresh = true` and is appended to a `cmds` batch instead
of returned directly.

**Verdict**: CONSOLIDATE — `(m *Model) buildRefreshCmd(isAutoRefresh bool) tea.Cmd` returning the
closure; the manual-refresh call site does `return m, m.buildRefreshCmd(false)`, the auto-refresh
site does `cmds = append(cmds, m.buildRefreshCmd(true))`.
**Behavior risk**: none found — bodies are byte-identical apart from the one field and the
return-vs-append difference, which stays at the call site.
**Tests**: no hits for `isAutoRefresh`/`preserveCursor`/a refresh-named test in `model_test.go` —
flag as untested; add a test asserting `isAutoRefresh` is `false`/`true` correctly for the two paths
before consolidating.

---

## 17. cli list.py &harr; search.py: priority-color formatting (found by reading, not by this run's jscpd)

**Repeated fact**: `list.py`'s `if priority_val == "HIGH": ... elif == "MEDIUM": ... elif == "LOW":
... else: ...` chain (`list.py:123-130`) and `search.py`'s identical chain (`search.py:93-100`) are
line-for-line the same 8-line `if/elif/else`, differing only in the variable feeding
`priority_val` one line above (`entry.get("priority")` vs `result.get("priority")`, which is itself
outside the matched block). As noted at the top of this document, this pair is real duplication that
this run's jscpd invocation does not report (the 8-line chain is ~29 tokens, under jscpd's default
50-token minimum — confirmed by checking the smallest block this run *did* report, 6 lines, and by
recomputing the token count by hand).

**Verdict**: CONSOLIDATE anyway, on the reading, not the tool: `formatPriority(priority_val: str) ->
str` returning the colored string, called from both `list.py`'s per-entry loop and `search.py`'s
per-result loop. Home: since both `list.py` and `search.py` already import `APIClient` from
`.api_client` (`list.py:9`, `search.py:7`), either add it there or a new small `cli/src/cli/
format.py` both already-adjacent files can import — do not put display formatting inside
`APIClient` itself, which is an HTTP client, not a rendering module.
**Behavior risk**: none — the two blocks are otherwise identical modulo the one external variable
name.
**Tests**: not investigated in depth (outside this run's jscpd-driven scope) — treat as untested
until checked.

---

## Splitting into build jobs

No two jobs below touch the same file, so they can run in parallel once opened. Order matters only
where noted.

- **Job: tui-client-dedup** — cluster 1 only (`tui/internal/api/client.go`). Touches one file; the
  test-gap work (adding `httptest`-backed tests for the four status-skipping methods) belongs in the
  same job since it's the thing that makes the consolidation safe to trust.
- **Job: tui-db-dedup** — clusters 4a/4b (`tui/internal/db/queries.go`). Independent of every other
  tui job.
- **Job: tui-source-modal-cleanup** — clusters 5a, 5b, 6 (`tui/internal/ui/source_modal.go` +
  `tui/internal/ui/modal.go`). Do 5a (the delete) **first** within this job, including rewriting the
  two tests that assert on the dead `modal.content` field — that's a prerequisite for the file even
  compiling cleanly afterward, and it shrinks what's left to review in 5b/6. This job should run
  before or independent of tui-operations-dedup; it doesn't share files with it.
- **Job: tui-operations-dedup** — clusters 7, 8, 9 (`tui/internal/ui/operations/{sources,context,
  prune}.go`). All three files currently have zero tests (see each cluster's note) — this job should
  budget time to add real-HTTP-backed tests, not just extract helpers.
- **Job: tui-misc-dedup** — clusters 14, 15, 16 (`tui/internal/commands/registry.go`,
  `tui/internal/ui/helpers.go`, `tui/internal/ui/model.go`) — three unrelated small files, bundled
  only because each is a single self-contained, low-risk extraction; fine to run in parallel with
  every other tui job.
- **Job: daemon-storage-dedup** — clusters 2a-2d (`daemon/src/prismis_daemon/storage.py` only). Do
  2d (the canonical `_content_row_to_dict`) with the `get_latest_content_for_source` `user_feedback`
  question resolved explicitly (fix it and say so, or deliberately keep the omission and say why) —
  this is the one sub-cluster with a real behavior decision, the rest are mechanical.
- **Job: daemon-entrypoint-dedup** — clusters 10a, 10b (`daemon/src/prismis_daemon/__main__.py`
  only), plus the missing "service&rarr;light_service" test-fixture gap noted in 10b.
- **Job: daemon-llm-call-dedup** — cluster 11 (`context_analyzer.py`, `evaluator.py`,
  `summarizer.py`, plus a new `llm_call.py`). This is the one job that carries a product decision
  (should `context_analyzer` gain circuit-breaker protection) — flag it for a human call before
  building, rather than letting the extraction decide it implicitly. Independent of every storage/
  entrypoint job; can run in parallel with them.
- **Job: daemon-api-dedup** — cluster 12 (`daemon/src/prismis_daemon/api.py` only).
- **Job: cross-stack-analysis-dedup** — cluster 13 (`cli/src/cli/analyze.py` +
  `daemon/src/prismis_daemon/orchestrator.py`, and wherever `build_llm_analysis` lands). Should run
  **after** daemon-storage-dedup and daemon-llm-call-dedup are merged, since it reads from the same
  `orchestrator.py` file those don't touch but sits conceptually downstream of the daemon's analysis
  pipeline shape; not a hard file conflict, just a sequencing preference to avoid rebasing a
  cross-stack change against daemon-side churn.
- **Job: cli-client-dedup** — cluster 3 (`cli/src/cli/api_client.py`) and cluster 17
  (`cli/src/cli/list.py`, `cli/src/cli/search.py`, plus wherever `formatPriority` lands). These two
  don't share a file with each other but are both small cli-only changes; fine bundled or split.

If sequencing must be collapsed to a single priority order: **tui-source-modal-cleanup first**
(it's a deletion with a concrete test-honesty defect, the highest-value single fix in this triage),
then the two "real decision" jobs (**daemon-storage-dedup**'s 2d field question,
**daemon-llm-call-dedup**'s circuit-breaker question) before anything depending on their shape, then
the remaining mechanical extractions in any order.
