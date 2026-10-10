#!/usr/bin/env python3
"""Measure what GET /api/entries costs the daemon in resident memory.

Runs the full-content and the slim (view=list) request for the same item count through
the real app, each in its own process so one request's peak never inflates the other's,
and reports peak RSS and response size for each. Exits 1 when the full request's peak
exceeds the slim request's by more than --max-delta-mb.

Point it at a COPY of a production database: opening the file runs the schema
migrations and SQLite writes beside it.

    uv run --project daemon daemon/scripts/memory_probe.py --db /path/to/copy.db

The response body is counted and dropped as it is sent, so the peak is the app's and
not a client holding the whole response.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

VIEWS = ("list", "full")


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _run_request(db: Path, view: str, limit: int) -> dict[str, Any]:
    """One request in this process; prints its figures as a JSON line."""
    data_home = Path(tempfile.mkdtemp(prefix="memory-probe-"))
    (data_home / "prismis").mkdir()
    (data_home / "prismis" / "prismis.db").symlink_to(db.resolve())
    os.environ["XDG_DATA_HOME"] = str(data_home)

    from prismis_daemon.api import app
    from prismis_daemon.auth import verify_api_key

    app.dependency_overrides[verify_api_key] = lambda: "probe"

    sent = 0
    status = 0

    async def send(message: dict[str, Any]) -> None:
        nonlocal sent, status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            sent += len(message.get("body", b""))

    requested = False

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # a connected client sends nothing more
        raise AssertionError("unreachable")

    query = f"limit={limit}" + chr(38) + f"view={view}"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/entries",
        "raw_path": b"/api/entries",
        "root_path": "",
        "query_string": query.encode(),
        "headers": [],
        "client": ("probe", 0),
        "server": ("probe", 80),
    }
    baseline = _peak_rss_mb()
    asyncio.run(app(scope, receive, send))
    return {
        "view": view,
        "status": status,
        "body_mb": round(sent / (1024 * 1024), 1),
        "baseline_rss_mb": round(baseline, 1),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="database copy to read")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--max-delta-mb", type=float, default=100.0)
    parser.add_argument("--child", choices=VIEWS, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.child:
        print(json.dumps(_run_request(args.db, args.child, args.limit)))
        return 0

    results: dict[str, dict[str, Any]] = {}
    for view in VIEWS:
        proc = subprocess.run(
            [sys.executable, __file__, "--db", str(args.db), "--limit", str(args.limit),
             "--child", view],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            return 1
        results[view] = json.loads(proc.stdout.strip().splitlines()[-1])
        print(json.dumps(results[view]))

    for view, row in results.items():
        if row["status"] != 200:
            print(f"FAIL: view={view} answered {row['status']}")
            return 1
    delta = results["full"]["peak_rss_mb"] - results["list"]["peak_rss_mb"]
    verdict = "PASS" if delta <= args.max_delta_mb else "FAIL"
    print(f"{verdict}: full peak exceeds list peak by {delta:.1f} MB (limit {args.max_delta_mb:.0f} MB)")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
