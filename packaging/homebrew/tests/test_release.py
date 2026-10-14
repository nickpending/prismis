"""Pin the release logic the tag workflow runs, entered through its command line.

Each case builds a stand-in repo in tmp_path (the real formulas, the real cli/uv.lock,
pyproject files with chosen versions), a fake tarball, and a directory standing in for the
tap checkout, then runs ``release.py`` as the workflow does.
"""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import render

HERE = Path(__file__).resolve().parent.parent
REAL_REPO = HERE.parent.parent
RELEASE = HERE / "release.py"
FORMULAS = ("prismis-tui", "prismis-cli", "prismis-daemon")
TARBALL_BYTES = b"prismis v1.4.2 source tarball"


def make_repo(root: Path, daemon: str = "1.4.2", cli: str = "1.4.2") -> Path:
    (root / "packaging" / "homebrew").mkdir(parents=True)
    for name in FORMULAS:
        shutil.copy(HERE / f"{name}.rb", root / "packaging" / "homebrew" / f"{name}.rb")
    (root / "cli").mkdir()
    shutil.copy(REAL_REPO / "cli" / "uv.lock", root / "cli" / "uv.lock")
    (root / "daemon").mkdir()
    for unit, version in (("daemon", daemon), ("cli", cli)):
        (root / unit / "pyproject.toml").write_text(
            f'[project]\nname = "prismis-{unit}"\nversion = "{version}"\n'
        )
    return root


def run_release(
    repo: Path, tap: Path, tarball: Path, tag: str = "v1.4.2"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RELEASE), tag, str(tarball), str(tap), "--repo-root", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def snapshot(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def stale_resources(formula: Path) -> None:
    """Corrupt one resource hash so the block no longer equals the lock's render."""
    text = formula.read_text()
    start, _ = render._resources_span(text)
    formula.write_text(text[:start] + text[start:].replace('sha256 "', 'sha256 "00', 1))


@pytest.fixture
def tarball(tmp_path: Path) -> Path:
    t = tmp_path / "prismis.tar.gz"
    t.write_bytes(TARBALL_BYTES)
    return t


@pytest.fixture
def tap(tmp_path: Path) -> Path:
    d = tmp_path / "tap"
    d.mkdir()
    return d


# ------------------------------------------------------------------ SC-1


def test_matching_versions_write_the_three_filled_formulas_into_the_tap(
    tmp_path: Path, tap: Path, tarball: Path
) -> None:
    repo = make_repo(tmp_path / "repo")
    committed = snapshot(repo / "packaging" / "homebrew")
    sha = hashlib.sha256(TARBALL_BYTES).hexdigest()

    result = run_release(repo, tap, tarball)

    assert result.returncode == 0, result.stderr
    assert sorted(snapshot(tap)) == [f"Formula/{n}.rb" for n in sorted(FORMULAS)]
    for name in FORMULAS:
        written = (tap / "Formula" / f"{name}.rb").read_text()
        template = (repo / "packaging" / "homebrew" / f"{name}.rb").read_text()
        expected = re.sub(
            r'^  url ".*"$', f'  url "{render.TAG_URL.format(tag="v1.4.2")}"', template, flags=re.M
        )
        expected = re.sub(r'^  sha256 ".*"$', f'  sha256 "{sha}"', expected, flags=re.M)
        expected = re.sub(r'^  version ".*"$', '  version "1.4.2"', expected, flags=re.M)
        assert written == expected
    assert snapshot(repo / "packaging" / "homebrew") == committed
    assert "v1.4.2" in result.stdout
    assert sha in result.stdout


# ------------------------------------------------------------------ SC-2


@pytest.mark.parametrize(
    ("daemon", "cli", "named"), [("1.4.1", "1.4.2", "daemon"), ("1.4.2", "1.4.3", "cli")]
)
def test_a_version_mismatch_exits_nonzero_naming_it_and_leaves_the_tap_untouched(
    tmp_path: Path, tap: Path, tarball: Path, daemon: str, cli: str, named: str
) -> None:
    repo = make_repo(tmp_path / "repo", daemon=daemon, cli=cli)
    (tap / "keep.txt").write_text("existing tap content")
    before = snapshot(tap)

    result = run_release(repo, tap, tarball)

    assert result.returncode != 0
    assert f"{named}/pyproject.toml" in result.stderr
    assert "1.4.2" in result.stderr
    assert snapshot(tap) == before


def test_a_stale_cli_formula_resource_block_exits_nonzero_and_leaves_the_tap_untouched(
    tmp_path: Path, tap: Path, tarball: Path
) -> None:
    repo = make_repo(tmp_path / "repo")
    stale_resources(repo / "packaging" / "homebrew" / "prismis-cli.rb")
    before = snapshot(tap)

    result = run_release(repo, tap, tarball)

    assert result.returncode != 0
    assert "resources differ" in result.stderr
    assert snapshot(tap) == before


def test_a_tag_without_the_v_prefix_is_refused_and_the_tap_untouched(
    tmp_path: Path, tap: Path, tarball: Path
) -> None:
    repo = make_repo(tmp_path / "repo")
    result = run_release(repo, tap, tarball, tag="1.4.2")
    assert result.returncode != 0
    assert snapshot(tap) == {}


# ------------------------------------------------------------------ SC-3

WORKFLOW = REAL_REPO / ".github" / "workflows" / "release.yml"


def test_workflow_triggers_on_v_tags_and_holds_only_contents_read() -> None:
    text = WORKFLOW.read_text()
    assert re.search(r'^on:\n  push:\n    tags: \["v\*"\]$', text, re.M)
    assert re.search(r"^permissions:\n  contents: read\n(?!  )", text, re.M)
    assert text.count("permissions:") == 1


def test_workflow_pins_every_action_by_commit_hash() -> None:
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", WORKFLOW.read_text(), re.M)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"not pinned by hash: {ref}"


def test_every_workflow_pins_an_action_to_the_same_commit() -> None:
    # A Dependabot PR opened before a workflow file existed bumps only the files it saw,
    # so the release would run an action CI no longer proves.
    pins: dict[str, dict[str, list[str]]] = {}
    for workflow in sorted(WORKFLOW.parent.glob("*.yml")):
        for ref in re.findall(r"^\s*-?\s*uses:\s*(\S+)", workflow.read_text(), re.M):
            action, _, sha = ref.partition("@")
            pins.setdefault(action, {}).setdefault(sha, []).append(workflow.name)
    assert "astral-sh/setup-uv" in pins
    drifted = {action: shas for action, shas in pins.items() if len(shas) > 1}
    assert not drifted, f"one action pinned to different commits: {drifted}"


def test_workflow_runs_the_release_logic_retries_on_404_and_pushes_the_tap_over_ssh() -> None:
    text = WORKFLOW.read_text()
    assert "release.py" in text
    assert "for attempt in 1 2 3" in text
    assert '"$code" = "404"' in text
    assert "secrets.TAP_DEPLOY_KEY" in text
    assert "git@github.com:nickpending/homebrew-prismis.git" in text
    assert text.count("git push") == 1
    assert len(re.findall(r"\bcommit -m\b", text)) == 1
