# browser-ua — post-merge walk evidence

## C3 — passed

Operator walk on <host>, the operator's Linux host, run 2026-10-31 05:40 UTC by the
orchestrating session and relayed to the lane driver. Every check below was read directly on the
host after the restart.

**When:** after merge, the change is deployed to the operator's Linux host and the daemon restarts.
**Then:** its database reports user_version 3 with a backup beside it, every source keeps its
fields, and the next fetch cycle completes with the same sources active as before.

### Deploy

- `cd ~/prismis && git pull --ff-only` moved main from fd294a2 to 44c0439.
- `make install-daemon` and `make install-cli-local` succeeded.
- `systemctl restart prismis`; the service is active.

### 1. Database

- `PRAGMA user_version` on `~/.local/share/prismis/prismis.db` reads 3. It read 2 before the
  deploy.
- A backup was written beside it: `~/.local/share/prismis/prismis.db.bak-tz`, 847,212,544 bytes,
  dated 05:40. None existed before.

### 2. Sources

- `SELECT count(*), sum(active), sum(naughty) FROM sources` reads `49|49|0`, both straight after
  the restart and after the first cycle.
- By type: file 2, reddit 6, rss 29, youtube 12, the same as before the deploy.
- Every source kept its fields, and none is marked naughty.

### 3. Fetch cycle

- The daemon started a cycle on restart (`daemon.cycle.start` at 05:40:35) and completed it at
  05:45:42.
- `duration_ms` 306496, `items_fetched` 222, `items_new` 4, `items_updated` 6, `errors` 1,
  `deep_extract_failures` 0, `kind_classify_failures` 0.
- All 49 sources stayed active after the cycle.
- The one error comes from the O-1-1 fix. The RSS source
  `https://www.nccgroup.com/us/research-blog/feed/` answers 404. It is now recorded as
  `fetcher.error` ("Client error '404 Not Found'") instead of an empty success.

### Noted, not counted against the walk

One traceback appeared in the journal at 05:40:24, before "Started prismis.service". It is the
stopping process's multiprocessing finalizer (a `sem_unlink` FileNotFoundError at shutdown). The
orchestrator reports the same noise on earlier restarts and says it does not come from the new
code.
