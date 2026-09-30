#!/usr/bin/env python3
"""Compare the summarizer's one prompt against the same steps run as separate calls (gh #79).

Two phases, run on different machines because each needs what only one has:

  generate  (where the LLM keys and the database live -- cerebro)
      Samples stored content read-only, runs each item through the production
      system prompt once ("single") and through each of its STEP sections as its
      own call ("split"), on the same service and the same user prompt, and writes
      every output with its tokens, billed cost and latency to a JSONL file.

          uv run --project daemon daemon/scripts/prompt_split_playtest.py generate \
              --service prismis-light --per-stratum 10 --out split.jsonl

  judge     (where `claude` is logged in -- the operator's machine)
      Scores each field group blind (single vs split in random order) through
      `claude -p`, and checks what code can decide on its own: quotes that are not
      verbatim, URLs that are not in the content or are the item's own URL.

          uv run --project daemon daemon/scripts/prompt_split_playtest.py judge \
              --in split.jsonl --out verdicts.jsonl

The split prompts are sliced out of the production prompt, not rewritten, so the
comparison is between the same instructions asked together and asked apart.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from model_playtest import DEFAULT_DB, sample_items

# Field groups, keyed by the STEP number that instructs them in the production prompt.
GROUPS: dict[str, tuple[int, list[str]]] = {
    "summaries": (1, ["summary", "reading_summary"]),
    "insights": (2, ["alpha_insights", "patterns"]),
    "quotes": (3, ["quotes"]),
    "tools": (4, ["tools"]),
    "urls": (5, ["urls"]),
}

_STEP_RE = re.compile(r"^STEP (\d+):", re.MULTILINE)


def split_prompts(system_prompt: str) -> dict[str, str]:
    """Slice one system prompt into a prompt per field group.

    Each keeps the prompt's preamble (role and JSON-only rule), its own STEP text,
    and the subset of the OUTPUT FORMAT example holding that group's keys.
    """
    head, sep, output_format = system_prompt.partition("\nOUTPUT FORMAT:\n")
    if not sep:
        raise ValueError("system prompt has no OUTPUT FORMAT section")
    example = json.loads(output_format)

    starts = [(int(m.group(1)), m.start()) for m in _STEP_RE.finditer(head)]
    if [n for n, _ in starts] != sorted(n for n, _ in GROUPS.values()):
        raise ValueError(f"expected STEP 1-5, found {[n for n, _ in starts]}")
    preamble = head[: starts[0][1]]
    bounds = {
        n: (s, e)
        for (n, s), (_, e) in zip(starts, starts[1:] + [(0, len(head))], strict=True)
    }

    prompts = {}
    for group, (step, keys) in GROUPS.items():
        s, e = bounds[step]
        subset = {k: example[k] for k in keys}
        prompts[group] = (
            f"{preamble}{head[s:e].rstrip()}\n\nOUTPUT FORMAT:\n"
            f"{json.dumps(subset, indent=2)}"
        )
    return prompts


def _call(service: str, system_prompt: str, user_prompt: str, action: str) -> dict:
    from prismis_daemon.llm_call import call_llm_with_circuit_breaker
    from prismis_daemon.llm_client import extract_json

    t0 = time.monotonic()
    try:
        result = call_llm_with_circuit_breaker(
            service, system_prompt, user_prompt, action
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    parsed = extract_json(result.text)
    return {
        "ok": isinstance(parsed, dict),
        "parsed": parsed if isinstance(parsed, dict) else None,
        "raw": None if isinstance(parsed, dict) else result.text[:500],
        "tokens_in": result.tokens.input,
        "tokens_out": result.tokens.output,
        "cost": result.cost,
        "ms": int((time.monotonic() - t0) * 1000),
    }


def generate(args: argparse.Namespace) -> int:
    from prismis_daemon.summarizer import ContentSummarizer

    summarizer = ContentSummarizer(args.service)
    items = [
        i
        for i in sample_items(args.db, args.per_stratum, args.seed)
        if i["source_type"] != "file"  # file sources use the diff prompt, no STEPs
    ]
    print(f"{len(items)} items, {1 + len(GROUPS)} calls each, service {args.service}")

    with args.out.open("w") as out:
        for idx, item in enumerate(items, 1):
            content = item["content"]
            source_type = item["source_type"] or "rss"
            word_count = summarizer._calculate_word_count(content)
            system_prompt = summarizer._select_system_prompt(word_count, source_type)
            user_prompt = summarizer._build_prompt(
                content,
                item["title"] or "",
                item["url"] or "",
                source_type,
                item["source_name"] or "",
                {},
            )
            single = _call(args.service, system_prompt, user_prompt, "summarize")
            split = {
                group: _call(args.service, prompt, user_prompt, f"summarize_{group}")
                for group, prompt in split_prompts(system_prompt).items()
            }
            record = {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "source_type": source_type,
                "priority": item["priority"],
                "mode": summarizer._get_mode_name(word_count, source_type),
                "content": content,
                "single": single,
                "split": split,
            }
            out.write(json.dumps(record) + "\n")
            out.flush()
            ok = single["ok"] and all(s["ok"] for s in split.values())
            print(
                f"  [{idx}/{len(items)}] {'ok' if ok else 'FAIL'}  {item['title'][:60]}"
            )
    return 0


# --- judge -----------------------------------------------------------------

JUDGE_SYSTEM = """You judge two analyses, A and B, of the same source content. They were \
produced from the same instructions; which is which is hidden from you and randomized per item.

For each field group, decide which analysis does that group's job better against the \
source content and the instructions below, or "tie" when neither is meaningfully better. \
Judge faithfulness to the source first, then usefulness to a reader. Longer is not better.

- summaries: summary (card text, 400 chars max) and reading_summary (markdown digest).
- insights: alpha_insights (universal truths grounded in the content) and patterns \
(specific methods or frameworks described).
- quotes: 0-3 genuinely insightful verbatim quotes; zero is correct when nothing qualifies.
- tools: software tools discussed substantively, not merely named. For each side, also \
count how many listed entries are actually software tools discussed substantively.
- urls: URLs referenced within the content, excluding the source's own URL.

Give a one-sentence reason per group naming the concrete difference."""

_GROUP_VERDICT = {
    "type": "object",
    "properties": {
        "winner": {"enum": ["A", "B", "tie"]},
        "reason": {"type": "string"},
    },
    "required": ["winner", "reason"],
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        **{g: _GROUP_VERDICT for g in GROUPS},
        "tools_valid_A": {"type": "integer"},
        "tools_valid_B": {"type": "integer"},
    },
    "required": [*GROUPS, "tools_valid_A", "tools_valid_B"],
}


def _fields(record: dict, variant: str) -> dict[str, Any] | None:
    if variant == "single":
        return record["single"]["parsed"] if record["single"]["ok"] else None
    merged: dict[str, Any] = {}
    for group, (_, keys) in GROUPS.items():
        call = record["split"][group]
        if not call["ok"]:
            return None
        merged.update({k: call["parsed"].get(k) for k in keys})
    return merged


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def code_checks(record: dict, fields: dict[str, Any]) -> dict[str, int]:
    content = _norm(record["content"])
    quotes = [q for q in fields.get("quotes") or [] if isinstance(q, str)]
    urls = [u for u in fields.get("urls") or [] if isinstance(u, str)]
    own = (record["url"] or "").rstrip("/")
    return {
        "quotes": len(quotes),
        "quotes_not_verbatim": sum(
            _norm(q).strip("\"'") not in content for q in quotes
        ),
        "urls": len(urls),
        "urls_not_in_content": sum(u.rstrip("/").lower() not in content for u in urls),
        "urls_own": sum(u.rstrip("/") == own for u in urls),
        "tools": len(fields.get("tools") or []),
    }


def _ask_claude(prompt: str, model: str, cwd: Path) -> dict:
    proc = subprocess.run(
        [
            "claude",
            "-p",
            "--model",
            model,
            "--setting-sources",
            "",
            "--tools",
            "",
            "--no-session-persistence",
            "--system-prompt",
            JUDGE_SYSTEM,
            "--json-schema",
            json.dumps(JUDGE_SCHEMA),
            "--output-format",
            "json",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=600,
    )
    reply = json.loads(proc.stdout)
    if reply.get("is_error") or not isinstance(reply.get("structured_output"), dict):
        raise RuntimeError(
            f"claude -p failed: {reply.get('result', proc.stderr)[:300]}"
        )
    return reply["structured_output"]


def judge(args: argparse.Namespace) -> int:
    records = [json.loads(line) for line in args.inp.open()]
    rng = random.Random(args.seed)
    done = set()
    if args.out.exists():
        done = {json.loads(line)["id"] for line in args.out.open()}

    with args.out.open("a") as out:
        for idx, record in enumerate(records, 1):
            if record["id"] in done:
                continue
            single, split = _fields(record, "single"), _fields(record, "split")
            if single is None or split is None:
                out.write(
                    json.dumps({"id": record["id"], "skipped": "a call failed"}) + "\n"
                )
                continue
            a_is_single = rng.random() < 0.5
            a, b = (single, split) if a_is_single else (split, single)
            prompt = (
                f"Source title: {record['title']}\nSource URL: {record['url']}\n\n"
                f"SOURCE CONTENT:\n{record['content']}\n\n"
                f"ANALYSIS A:\n{json.dumps(a, indent=2)}\n\n"
                f"ANALYSIS B:\n{json.dumps(b, indent=2)}"
            )
            verdict = _ask_claude(prompt, args.model, args.out.parent)
            unblind = {"A": "single" if a_is_single else "split"}
            unblind["B"] = "split" if a_is_single else "single"
            row = {
                "id": record["id"],
                "title": record["title"],
                "winners": {
                    g: unblind.get(verdict[g]["winner"], "tie") for g in GROUPS
                },
                "reasons": {g: verdict[g]["reason"] for g in GROUPS},
                "tools_valid": {
                    unblind["A"]: verdict["tools_valid_A"],
                    unblind["B"]: verdict["tools_valid_B"],
                },
                "checks": {
                    "single": code_checks(record, single),
                    "split": code_checks(record, split),
                },
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"  [{idx}/{len(records)}] {row['winners']}")

    print(report(records, [json.loads(line) for line in args.out.open()]))
    return 0


def report(records: list[dict], verdicts: list[dict]) -> str:
    lines = ["", "cost, tokens and latency per item (median; split = sum of its calls)"]
    for variant in ("single", "split"):
        calls = [
            [r["single"]] if variant == "single" else list(r["split"].values())
            for r in records
        ]
        ok = [c for c in calls if all(x["ok"] for x in c)]
        per = {
            k: statistics.median(sum(x.get(k) or 0 for x in c) for c in ok) if ok else 0
            for k in ("cost", "tokens_in", "tokens_out", "ms")
        }
        lines.append(
            f"  {variant:6s} ok {len(ok)}/{len(calls)}  ${per['cost']:.5f}  "
            f"in {per['tokens_in']:.0f}  out {per['tokens_out']:.0f}  {per['ms'] / 1000:.1f}s"
        )

    judged = [v for v in verdicts if "winners" in v]
    lines += ["", f"blind judge, {len(judged)} items (single / split / tie)"]
    for g in GROUPS:
        tally = {
            w: sum(v["winners"][g] == w for v in judged)
            for w in ("single", "split", "tie")
        }
        lines.append(
            f"  {g:10s} {tally['single']:3d} / {tally['split']:3d} / {tally['tie']:3d}"
        )

    lines += ["", "code checks, totals (single / split)"]
    for key in (
        "quotes",
        "quotes_not_verbatim",
        "urls",
        "urls_not_in_content",
        "urls_own",
        "tools",
    ):
        s = sum(v["checks"]["single"][key] for v in judged)
        p = sum(v["checks"]["split"][key] for v in judged)
        lines.append(f"  {key:20s} {s:4d} / {p:4d}")
    s = sum(v["tools_valid"]["single"] for v in judged)
    p = sum(v["tools_valid"]["split"] for v in judged)
    lines.append(f"  {'tools judged valid':20s} {s:4d} / {p:4d}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)

    gen = sub.add_parser("generate")
    gen.add_argument("--service", default="prismis-light")
    gen.add_argument("--per-stratum", type=int, default=10)
    gen.add_argument("--seed", type=int, default=1337)
    gen.add_argument("--db", type=Path, default=DEFAULT_DB)
    gen.add_argument("--out", type=Path, required=True)

    jdg = sub.add_parser("judge")
    jdg.add_argument("--in", dest="inp", type=Path, required=True)
    jdg.add_argument("--out", type=Path, required=True)
    jdg.add_argument("--model", default="opus")
    jdg.add_argument("--seed", type=int, default=1337)

    args = parser.parse_args()
    return generate(args) if args.phase == "generate" else judge(args)


if __name__ == "__main__":
    sys.exit(main())
