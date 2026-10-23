"""release_notes.py over a real git repository shaped like prismis's history.

The repository holds every commit shape the notes must sort: a work order's lane
bookkeeping and its close (with work-order.json at the close), a lane merge that puts a
commit made on main during the lane on its second parent, a pull-request merge with its own
commit, a direct fix, a docs commit and the release commit. Expected notes are written out
by hand below, not derived from the code under test.
"""

import json
import subprocess
from pathlib import Path

import pytest

import release_notes


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(
    repo: Path, message: str, path: str = "f.txt", text: str | None = None
) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text if text is not None else message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def history(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "first")
    _git(repo, "tag", "v0.1.0")

    # A work order: lane bookkeeping on main, the job built on a branch, and a direct
    # commit made on main while the lane ran, which the lane merge takes as parent 2.
    _commit(repo, "fast-thing: shape — problem", "docs/work/fast-thing/problem.json")
    _git(repo, "checkout", "-q", "-b", "job")
    _commit(repo, "fast-thing: build job 1 — 2 files", "src/fast.py")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "docs: explain the fast thing", "README.md")
    _git(repo, "checkout", "-q", "job")
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "fast-thing: merge job 1 (worktree-x)",
        "main",
    )
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "job")
    order = {
        "workOrder": "fast-thing",
        "what": "Make the thing fast.",
        "issues": [7, 9],
    }
    _commit(
        repo,
        "fast-thing: close — C1, C2 delivered",
        "docs/work/fast-thing/work-order.json",
        json.dumps(order),
    )

    # A pull request: its own commit and the merge GitHub writes for it.
    _git(repo, "checkout", "-q", "-b", "contributor/fix")
    _commit(repo, "fix(cli): handle the empty case", "cli.py")
    _git(repo, "checkout", "-q", "main")
    _git(
        repo, "merge", "-q", "--no-ff", "contributor/fix",
        "-m", "Merge pull request #12 from contributor/fix",
        "-m", "Handle the empty case in the CLI",
    )  # fmt: skip

    _commit(repo, "api: stop dropping the last item", "api.py")
    _commit(repo, "ci(release): pin the action", ".github/release.yml")
    _commit(repo, "Release v0.2.0", "pyproject.toml")
    _git(repo, "tag", "v0.2.0")
    return repo


def test_notes_list_each_change_once_in_its_section(history: Path) -> None:
    """
    INVARIANT: every change in the range appears exactly once in its section: a work
               order at its close with its own what and issues, a direct commit by
               subject, a pull request by its title, and nothing from lane bookkeeping
               or the release commit
    BREAKS: reading first-parent history drops the docs commit the lane merge took as
            parent 2 (the README rewrite missing from prismis's v0.3.3..HEAD); listing
            a pull request's own commits beside its merge lists the fix twice
    """
    notes = release_notes.collect(history, "v0.1.0", "v0.2.0")
    sha = {s: _git(history, "log", "--format=%h", "-1", "--grep", s, "--fixed-strings") for s in (
        "docs: explain the fast thing", "api: stop dropping the last item", "ci(release): pin the action",
    )}  # fmt: skip

    assert notes.delivered == ["**fast-thing**: Make the thing fast. (closes #7, #9)"]
    assert notes.changes == [
        f"api: stop dropping the last item ({sha['api: stop dropping the last item']})"
    ]
    assert notes.pull_requests == ["Handle the empty case in the CLI (#12)"]
    assert notes.maintenance == [
        f"docs: explain the fast thing ({sha['docs: explain the fast thing']})",
        f"ci(release): pin the action ({sha['ci(release): pin the action']})",
    ]


def test_rendered_notes_carry_sections_in_order_and_the_compare_link(
    history: Path,
) -> None:
    """
    INVARIANT: the markdown has one section per non-empty list in a fixed order and
               ends with the compare link for the range
    BREAKS: an empty section printed as a bare heading, or a missing compare link
    """
    text = release_notes.render(
        release_notes.collect(history, "v0.1.0", "v0.2.0"), "v0.1.0", "v0.2.0", "o/r"
    )

    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Delivered",
        "## Fixes and changes",
        "## Pull requests",
        "## Docs and maintenance",
    ]
    assert text.rstrip().endswith(
        "**Full changelog**: https://github.com/o/r/compare/v0.1.0...v0.2.0"
    )


def test_a_close_without_a_work_order_falls_back_to_its_subject(tmp_path: Path) -> None:
    """
    INVARIANT: a close commit whose work-order.json is absent is still listed, by slug
               and subject, rather than failing the notes
    BREAKS: an older work order that predates work-order.json aborts the release notes
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "first")
    _git(repo, "tag", "v1.0.0")
    _commit(repo, "old-thing: close — SC-1 delivered", "x.txt")
    _git(repo, "tag", "v1.1.0")

    notes = release_notes.collect(repo, "v1.0.0", "v1.1.0")

    assert notes.delivered == ["**old-thing**: old-thing: close — SC-1 delivered"]


def test_an_unknown_ref_exits_nonzero_naming_it(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    INVARIANT: a range whose ref does not exist fails with exit 1 and says why on
               stderr, printing no notes
    BREAKS: a typo in PREV yields empty notes that look like 'no changes'
    """
    code = release_notes.main(["v9.9.9", "v0.2.0", "--repo-root", str(history)])

    out = capsys.readouterr()
    assert code == 1
    assert out.out == ""
    assert "v9.9.9" in out.err
