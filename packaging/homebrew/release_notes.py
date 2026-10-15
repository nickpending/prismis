"""Release notes for a tag, from the history GitHub's pull-request notes cannot see.

Usage:
    release_notes.py PREV TAG [--repo-root R] [--repo OWNER/NAME]

Most prismis work lands on main directly, so GitHub's generated notes (pull requests only)
miss it. This reads every non-merge commit in PREV..TAG instead, plus one entry per
pull-request merge; a pull request's own commits (``merge^1..merge^2``) are listed through
that entry, not again. First-parent history is not used: a lane's merge can put commits
made on main during the lane on its second parent. A work order is listed once, at its
``<slug>: close`` commit, described by the work-order.json it closed with; its shape,
dispatch, build, merge and checkpoint commits and the release commit are bookkeeping and
are left out. Prints markdown on stdout.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SLUG = r"[a-z0-9][a-z0-9-]*"
_LANE = re.compile(
    rf"^{_SLUG}: (shape —|dispatch |build job |merge job |checkpoint|work order for )"
)
_CLOSE = re.compile(rf"^({_SLUG}): close — .* delivered$")
_RELEASE = re.compile(r"^Release v\d+\.\d+\.\d+$")
_MERGE_PR = re.compile(r"^Merge pull request #(\d+) from \S+$")
_MAINTENANCE = re.compile(r"^(docs|ci|build|chore|deps|test|tests)(\(.*?\))?: ")


class NotesError(Exception):
    pass


@dataclass
class Notes:
    delivered: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    pull_requests: list[str] = field(default_factory=list)
    maintenance: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise NotesError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _work_order_line(repo: Path, sha: str, slug: str, subject: str) -> str:
    """`**slug**: what (closes #n)` from the work order as it stood at its close."""
    try:
        doc = json.loads(_git(repo, "show", f"{sha}:docs/work/{slug}/work-order.json"))
    except NotesError:
        return f"**{slug}**: {subject}"
    line = f"**{slug}**: {doc['what']}"
    issues = doc.get("issues") or []
    if issues:
        line += " (closes " + ", ".join(f"#{n}" for n in issues) + ")"
    return line


def _commits(repo: Path, *args: str) -> list[tuple[str, str, str]]:
    """(sha, subject, body) for `git log ARGS`, oldest first."""
    log = _git(repo, "log", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", *args)
    records = []
    for record in log.split("\x1e"):
        if record.strip():
            sha, subject, body = record.strip("\n").split("\x1f", 2)
            records.append((sha, subject, body))
    return records


def collect(repo: Path, prev: str, tag: str) -> Notes:
    """Sort the commits of PREV..TAG into the notes' sections, oldest first."""
    for ref in (prev, tag):
        _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    notes = Notes()
    in_pull_requests: set[str] = set()
    for sha, subject, body in _commits(repo, "--merges", f"{prev}..{tag}"):
        if pr := _MERGE_PR.match(subject):
            title = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            notes.pull_requests.append(f"{title or subject} (#{pr.group(1)})")
            in_pull_requests.update(
                _git(repo, "rev-list", f"{sha}^1..{sha}^2").split()
            )
    for sha, subject, _body in _commits(repo, "--no-merges", f"{prev}..{tag}"):
        if sha in in_pull_requests or _RELEASE.match(subject) or _LANE.match(subject):
            continue
        if close := _CLOSE.match(subject):
            notes.delivered.append(_work_order_line(repo, sha, close.group(1), subject))
        elif _MAINTENANCE.match(subject):
            notes.maintenance.append(f"{subject} ({sha[:7]})")
        else:
            notes.changes.append(f"{subject} ({sha[:7]})")
    return notes


def render(notes: Notes, prev: str, tag: str, repo_slug: str | None) -> str:
    sections = [
        ("Delivered", notes.delivered),
        ("Fixes and changes", notes.changes),
        ("Pull requests", notes.pull_requests),
        ("Docs and maintenance", notes.maintenance),
    ]
    parts = [
        f"## {heading}\n\n" + "\n".join(f"- {line}" for line in lines)
        for heading, lines in sections
        if lines
    ]
    if not parts:
        parts.append("No changes since " + prev + ".")
    if repo_slug:
        parts.append(
            f"**Full changelog**: https://github.com/{repo_slug}/compare/{prev}...{tag}"
        )
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prev")
    parser.add_argument("tag")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--repo", help="OWNER/NAME for the compare link")
    args = parser.parse_args(argv)
    try:
        notes = collect(args.repo_root, args.prev, args.tag)
    except NotesError as e:
        print(f"release_notes: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(render(notes, args.prev, args.tag, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
