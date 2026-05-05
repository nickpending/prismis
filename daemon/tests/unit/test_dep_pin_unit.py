"""Tests for task 2.5: Dependency pinning invariants.

Covers:
- INV-DEP-1: Every [tool.uv.sources] git source MUST carry a rev= field
- INV-DEP-1 (format): rev= must be a commit hash, not a branch or tag name
- INV-DEP-1 (lockfile): uv.lock must contain the pinned rev in its llm-core stanza
- SC-17: llm-core source declaration carries the validated rev "2eb4429"
- SC-19: uv.lock reflects the pinned rev (full SHA)

Why these tests:
  A missing or wrong rev= in [tool.uv.sources] causes `uv tool install --reinstall`
  (used by `make install-daemon`) to silently advance to remote HEAD on every deploy.
  That breaks cerebro deployments without any prismis-side commit to blame.
"""

import re
import tomllib
from pathlib import Path

# Project root — two directories up from this test file:
# daemon/tests/unit/test_dep_pin_unit.py -> daemon/tests/unit -> daemon/tests -> daemon -> project root
_DAEMON_DIR = Path(__file__).parent.parent.parent
_PROJECT_ROOT = _DAEMON_DIR.parent
_PYPROJECT = _DAEMON_DIR / "pyproject.toml"
_LOCKFILE = _DAEMON_DIR / "uv.lock"

# The commit pinned in task 2.5: validated on cerebro 2026-04-29 with llm-core v0.3.1
_VALIDATED_REV = "2eb4429"
_VALIDATED_SHA = "2eb4429bb0c7619dcbe1159ccd4720d4f4294f2a"

# Minimal commit-hash pattern: 7-40 lowercase hex chars (git short or full SHA)
_COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")


def test_INVARIANT_every_git_source_has_rev_field() -> None:
    """
    INV-DEP-1: Every [tool.uv.sources] entry using a git source MUST carry rev=.
    BREAKS: `uv tool install --reinstall` silently advances to HEAD on cerebro deploy.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    assert sources, "No [tool.uv.sources] section found — section expected"

    missing_rev = []
    for name, entry in sources.items():
        if isinstance(entry, dict) and "git" in entry:
            if "rev" not in entry:
                missing_rev.append(name)

    assert missing_rev == [], (
        f"INV-DEP-1 FAILED — git sources missing rev= field: {missing_rev}\n"
        "Without rev=, `uv tool install --reinstall` resolves to HEAD on every deploy."
    )


def test_INVARIANT_git_rev_is_commit_hash_not_branch() -> None:
    """
    INV-DEP-1 (format): rev= values MUST be commit hashes, not branch/tag names.
    BREAKS: Branch names resolve to HEAD; tag names are mutable. Only commit hashes
    provide the reproducibility guarantee the policy requires.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    non_hash_revs = []
    for name, entry in sources.items():
        if isinstance(entry, dict) and "git" in entry and "rev" in entry:
            rev = entry["rev"]
            if not _COMMIT_HASH_RE.match(rev):
                non_hash_revs.append(f"{name}: rev={rev!r}")

    assert non_hash_revs == [], (
        "INV-DEP-1 (format) FAILED — non-hash rev values found:\n"
        + "\n".join(non_hash_revs)
        + "\nUse a commit SHA (7-40 hex chars), not a branch or tag name."
    )


def test_SC17_llm_core_source_pin_matches_validated_rev() -> None:
    """
    SC-17: llm-core source entry must carry rev= "2eb4429" (validated cerebro commit).
    BREAKS: A different rev would replace the validated v0.3.1 build with an unknown
    version that has not been verified on cerebro.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    assert "llm-core" in sources, "llm-core not found in [tool.uv.sources]"

    entry = sources["llm-core"]
    assert isinstance(entry, dict), f"Unexpected llm-core source shape: {entry!r}"
    assert "rev" in entry, "SC-17 FAILED — llm-core source entry has no rev= field"

    rev = entry["rev"]
    assert _VALIDATED_SHA.startswith(rev) or rev == _VALIDATED_SHA, (
        f"SC-17 FAILED — llm-core rev= {rev!r} does not match validated commit "
        f"{_VALIDATED_REV!r} (full SHA: {_VALIDATED_SHA})"
    )


def test_SC19_lockfile_contains_pinned_rev() -> None:
    """
    SC-19: daemon/uv.lock must contain the pinned rev in the llm-core stanza.
    BREAKS: If lockfile drifts from pyproject.toml, `uv lock --check` exits non-zero
    and deploy tooling may re-resolve from a stale or HEAD ref.
    """
    assert _LOCKFILE.exists(), f"uv.lock not found: {_LOCKFILE}"

    content = _LOCKFILE.read_text()

    # uv expands the short rev to full SHA in the lockfile URL.
    # Both the short rev (in the ?rev= query param) and the full SHA
    # (in the fragment #SHA) must be present in the llm-core stanza.
    assert _VALIDATED_REV in content, (
        f"SC-19 FAILED — pinned rev {_VALIDATED_REV!r} not found in uv.lock. "
        "Run `uv lock` from daemon/ to regenerate the lockfile."
    )
    assert _VALIDATED_SHA in content, (
        f"SC-19 FAILED — full SHA {_VALIDATED_SHA!r} not found in uv.lock. "
        "Lockfile may have been regenerated against a different rev."
    )


def test_INVARIANT_lockfile_exists_alongside_pyproject() -> None:
    """
    INV-DEP-2: daemon/uv.lock MUST exist alongside daemon/pyproject.toml.
    BREAKS: Without the lockfile, transitive deps are unresolved and the install
    is non-reproducible even with a source-level rev= pin in pyproject.toml.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"
    assert _LOCKFILE.exists(), (
        f"INV-DEP-2 FAILED — uv.lock not found at {_LOCKFILE}. "
        "The lockfile must exist and be committed alongside pyproject.toml."
    )
