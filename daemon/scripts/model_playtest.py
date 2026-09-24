#!/usr/bin/env python3
"""Compare candidate llm-core services against the stored baseline analysis.

Runs prismis's real summarize + evaluate call path over a stratified sample of
already-analyzed content and reports structured-output compliance, priority
agreement, cost, and latency per service.

Opens the prismis database read-only and never writes analysis back.

    uv run --project daemon daemon/scripts/model_playtest.py \
        --services prismis-pt-qwen,prismis-pt-deepseek --limit 10
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB = Path.home() / ".local" / "share" / "prismis" / "prismis.db"
STRATA: list[str | None] = ["high", "medium", "low", None]

SAMPLE_SQL = """
    SELECT c.id, c.title, c.url, c.content, c.summary, c.priority, c.analysis,
           s.type AS source_type, s.name AS source_name
    FROM content c
    JOIN sources s ON s.id = c.source_id
    WHERE {clause}
      AND c.content IS NOT NULL
      AND length(c.content) BETWEEN 500 AND 40000
      AND c.summary IS NOT NULL
      AND c.analysis IS NOT NULL
      AND c.archived_at IS NULL
    ORDER BY c.id
"""


def sample_items(db_path: Path, per_stratum: int, seed: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rng = random.Random(seed)
    picked: list[dict] = []
    try:
        for priority in STRATA:
            clause = "c.priority IS NULL" if priority is None else "c.priority = ?"
            args = () if priority is None else (priority,)
            rows = conn.execute(SAMPLE_SQL.format(clause=clause), args).fetchall()
            if not rows:
                print(f"  warning: no items in stratum {priority or '(none)'}")
                continue
            take = min(per_stratum, len(rows))
            if take < per_stratum:
                print(
                    f"  warning: stratum {priority or '(none)'} has only {len(rows)} "
                    f"eligible items, requested {per_stratum}"
                )
            picked.extend(dict(r) for r in rng.sample(rows, take))
    finally:
        conn.close()
    return picked


def run_service(service: str, items: list[dict], context: str) -> list[dict]:
    from prismis_daemon.evaluator import ContentEvaluator
    from prismis_daemon.summarizer import ContentSummarizer

    summarizer = ContentSummarizer(service)
    evaluator = ContentEvaluator(service)
    results = []

    for idx, item in enumerate(items, 1):
        row: dict = {
            "id": item["id"],
            "title": item["title"],
            "baseline_priority": item["priority"],
            "baseline_summary": item["summary"],
        }

        t0 = time.monotonic()
        try:
            summary = summarizer.summarize_with_analysis(
                content=item["content"],
                title=item["title"] or "",
                url=item["url"] or "",
                source_type=item["source_type"] or "rss",
                source_name=item["source_name"] or "",
                metadata={},
            )
            # summarize_with_analysis swallows provider and JSON-parse failures
            # into None, so a None here is the structured-output failure signal.
            row["summarize_ok"] = summary is not None
            if summary:
                row["summary"] = summary.summary
                row["reading_summary"] = summary.reading_summary
                row["entities"] = summary.entities
                row["alpha_insights"] = summary.alpha_insights
        except Exception as exc:
            row["summarize_ok"] = False
            row["summarize_error"] = f"{type(exc).__name__}: {exc}"
        row["summarize_ms"] = int((time.monotonic() - t0) * 1000)

        t1 = time.monotonic()
        try:
            evaluation = evaluator.evaluate_content(
                content=item["content"],
                title=item["title"] or "",
                url=item["url"] or "",
                context=context,
            )
            row["evaluate_ok"] = True
            row["priority"] = (
                evaluation.priority.value if evaluation.priority else None
            )
            row["matched_interests"] = evaluation.matched_interests
            row["reasoning"] = evaluation.reasoning
        except Exception as exc:
            row["evaluate_ok"] = False
            row["evaluate_error"] = f"{type(exc).__name__}: {exc}"
        row["evaluate_ms"] = int((time.monotonic() - t1) * 1000)

        flag = "ok" if row["summarize_ok"] and row["evaluate_ok"] else "FAIL"
        print(
            f"  [{idx}/{len(items)}] {flag:4s} "
            f"{str(row.get('baseline_priority') or '(none)'):6s} -> "
            f"{str(row.get('priority') or '(none)'):6s}  "
            f"{(item['title'] or '')[:58]}"
        )
        results.append(row)

    return results


def openrouter_prices() -> dict[str, tuple[float, float]]:
    """Live $/1M in-out rates keyed by OpenRouter model id.

    llm-core prices from litellm's table, which carries none of the OpenRouter
    model ids, so it reports cost_usd=None and the run would score $0 for every
    candidate. Price from the source of truth instead.
    """
    import httpx

    try:
        data = httpx.get("https://openrouter.ai/api/v1/models", timeout=20).json()
    except Exception as exc:
        print(f"  warning: could not fetch OpenRouter pricing ({exc}); costs omitted")
        return {}
    prices = {}
    for model in data.get("data", []):
        pricing = model.get("pricing") or {}
        try:
            prices[model["id"]] = (
                float(pricing["prompt"]) * 1e6,
                float(pricing["completion"]) * 1e6,
            )
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def read_call_log(log_dir: Path, model_filter: set[str] | None = None) -> dict:
    """Aggregate llm.call events the run wrote to its isolated observability dir."""
    totals: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "cost": 0.0, "in": 0, "out": 0, "priced": False}
    )
    for path in sorted(log_dir.glob("*_events.jsonl")):
        for line in path.read_text(errors="ignore").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "llm.call":
                continue
            model = event.get("model", "?")
            if model_filter and model not in model_filter:
                continue
            tokens = event.get("tokens") or {}
            bucket = totals[model]
            bucket["calls"] += 1
            bucket["cost"] += event.get("cost_usd") or 0.0
            bucket["in"] += tokens.get("prompt", 0)
            bucket["out"] += tokens.get("completion", 0)

    prices = openrouter_prices()
    for model, bucket in totals.items():
        rate = prices.get(model)
        if rate:
            bucket["cost"] = bucket["in"] / 1e6 * rate[0] + bucket["out"] / 1e6 * rate[1]
            bucket["priced"] = True
    return dict(totals)


def scoreboard(all_results: dict[str, list[dict]], usage: dict) -> str:
    lines = []
    lines.append("\n=== STRUCTURED-OUTPUT COMPLIANCE ===")
    lines.append(f"{'service':24s} {'summarize':>10s} {'evaluate':>10s} {'both':>8s}")
    for service, rows in all_results.items():
        n = len(rows)
        s_ok = sum(1 for r in rows if r.get("summarize_ok"))
        e_ok = sum(1 for r in rows if r.get("evaluate_ok"))
        both = sum(1 for r in rows if r.get("summarize_ok") and r.get("evaluate_ok"))
        lines.append(
            f"{service:24s} {s_ok}/{n:<7d} {e_ok}/{n:<7d} {100 * both / n:6.1f}%"
        )

    lines.append("\n=== PRIORITY AGREEMENT vs BASELINE ===")
    for service, rows in all_results.items():
        scored = [r for r in rows if r.get("evaluate_ok")]
        if not scored:
            lines.append(f"\n{service}: no successful evaluations")
            continue
        agree = sum(1 for r in scored if r["priority"] == r["baseline_priority"])
        matrix: Counter = Counter(
            (r["baseline_priority"] or "(none)", r["priority"] or "(none)")
            for r in scored
        )
        base_high = sum(1 for r in scored if r["baseline_priority"] == "high")
        cand_high = sum(1 for r in scored if r["priority"] == "high")
        lines.append(
            f"\n{service}: {agree}/{len(scored)} agree ({100 * agree / len(scored):.1f}%)"
        )
        lines.append(
            f"  HIGH volume: baseline {base_high} -> candidate {cand_high}"
            + (
                f"  ({cand_high / base_high:.1f}x notification load)"
                if base_high
                else ""
            )
        )
        labels = ["high", "medium", "low", "(none)"]
        lines.append("  baseline\\candidate " + "".join(f"{c:>9s}" for c in labels))
        for base in labels:
            row = "".join(f"{matrix.get((base, cand), 0):9d}" for cand in labels)
            lines.append(f"  {base:>18s} {row}")

    lines.append("\n=== COST & LATENCY (from this run's call log) ===")
    lines.append(
        f"{'model':40s} {'calls':>6s} {'cost':>9s} {'in tok':>9s} {'out tok':>9s}"
    )
    for model, stats in sorted(usage.items()):
        cost = f"${stats['cost']:8.4f}" if stats.get("priced") else "  unpriced"
        lines.append(
            f"{model:40s} {stats['calls']:6d} {cost:>9s} "
            f"{stats['in']:9d} {stats['out']:9d}"
        )

    lines.append("")
    for service, rows in all_results.items():
        s_lat = sorted(r["summarize_ms"] for r in rows if r.get("summarize_ok"))
        e_lat = sorted(r["evaluate_ms"] for r in rows if r.get("evaluate_ok"))
        if s_lat and e_lat:
            lines.append(
                f"{service:24s} summarize p50 {s_lat[len(s_lat) // 2] / 1000:5.1f}s  "
                f"evaluate p50 {e_lat[len(e_lat) // 2] / 1000:5.1f}s"
            )
    return "\n".join(lines)


def side_by_side(all_results: dict[str, list[dict]]) -> str:
    services = list(all_results)
    by_id: dict[str, dict] = defaultdict(dict)
    for service, rows in all_results.items():
        for row in rows:
            by_id[row["id"]][service] = row

    out = ["# Model playtest — side by side\n"]
    for item_id, per_service in by_id.items():
        first = next(iter(per_service.values()))
        out.append(f"\n## {first['title']}\n")
        out.append(f"`{item_id}`\n")
        out.append(
            f"**baseline priority:** {first['baseline_priority'] or '(none)'}\n"
        )
        out.append(f"**baseline summary:** {first['baseline_summary']}\n")
        for service in services:
            row = per_service.get(service)
            if not row:
                continue
            out.append(f"\n### {service}\n")
            if not row.get("summarize_ok"):
                out.append(
                    f"- summarize FAILED: {row.get('summarize_error', 'returned None')}\n"
                )
            else:
                out.append(f"- **summary:** {row.get('summary')}\n")
                out.append(f"- **entities:** {', '.join(row.get('entities') or [])}\n")
            if not row.get("evaluate_ok"):
                out.append(f"- evaluate FAILED: {row.get('evaluate_error')}\n")
            else:
                out.append(f"- **priority:** {row.get('priority') or '(none)'}\n")
                out.append(f"- **reasoning:** {row.get('reasoning')}\n")
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--services",
        default="prismis-pt-qwen,prismis-pt-deepseek,prismis-pt-luna,prismis-pt-ling",
        help="Comma-separated llm-core service names to test",
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Items per priority stratum"
    )
    parser.add_argument("--seed", type=int, default=1337, help="Sampling seed")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("playtest-runs") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the sample and exit without calling any model",
    )
    args = parser.parse_args()

    services = [s.strip() for s in args.services.split(",") if s.strip()]
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    # Redirect the daemon's observability singleton at the run directory so
    # playtest calls stay out of the production cost history and give this run
    # its own authoritative token/cost record.
    from prismis_daemon import observability

    observability._logger = observability.ObservabilityLogger(
        base_dir=out_dir / "events"
    )

    from prismis_daemon.config import Config

    config = Config.from_file()

    print(f"sampling {args.limit} items per stratum from {args.db}")
    items = sample_items(args.db, args.limit, args.seed)
    print(f"sampled {len(items)} items across {len(STRATA)} strata")

    if args.dry_run:
        for item in items:
            print(
                f"  {str(item['priority'] or '(none)'):6s} "
                f"{len(item['content']):7d}ch  {(item['title'] or '')[:64]}"
            )
        return 0

    all_results: dict[str, list[dict]] = {}
    for service in services:
        print(f"\n--- {service} ---")
        all_results[service] = run_service(service, items, config.context)

    usage = read_call_log(out_dir / "events")

    report = scoreboard(all_results, usage)
    print(report)

    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "seed": args.seed,
                "limit": args.limit,
                "services": services,
                "usage": usage,
                "results": all_results,
            },
            indent=2,
            default=str,
        )
    )
    (out_dir / "scoreboard.txt").write_text(report)
    (out_dir / "side-by-side.md").write_text(side_by_side(all_results))
    print(f"\nwrote {out_dir}/results.json, scoreboard.txt, side-by-side.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
