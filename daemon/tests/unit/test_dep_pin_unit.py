"""Tests for task 2.5: Dependency pinning invariants.

Covers:
- INV-DEP-1: Every [tool.uv.sources] git source MUST carry a rev= field
- INV-DEP-1 (format): rev= must be a commit hash, not a branch or tag name
- SC-10 (openai-sdk-migration): openai is pinned to a floor version on PyPI (not a
  git source, so INV-DEP-1 does not apply to it).
- SC-3 (self-contained-llm-config): the daemon declares no git-only key library and
  uv.lock carries none. With no git source left, the two INV-DEP-1 checks hold for
  any that is added later.

Why these tests:
  A missing or wrong rev= in [tool.uv.sources] causes `uv tool install --reinstall`
  (used by `make install-daemon`) to silently advance to remote HEAD on every deploy.
  That breaks cerebro deployments without any prismis-side commit to blame. An
  unpinned floor version on a PyPI dependency risks a breaking release resolving
  silently into a deploy the same way.
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

# Minimal commit-hash pattern: 7-40 lowercase hex chars (git short or full SHA)
_COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")


def test_INVARIANT_llm_core_git_source_is_gone() -> None:
    """
    SC-10 / SC-8: llm-core must not appear in [tool.uv.sources] any more -- its git
    source is exactly what this migration removes.
    BREAKS: A leftover llm-core source line reintroduces the git-rev drift risk for a
    dependency the daemon no longer imports.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    assert "llm-core" not in sources, (
        "llm-core still present in [tool.uv.sources] after the openai-sdk-migration "
        "removed it as a dependency"
    )

    dependencies = data.get("project", {}).get("dependencies", [])
    assert not any(dep.split(">=")[0].split("=")[0].strip() == "llm-core" for dep in dependencies), (
        "llm-core still present in [project.dependencies]"
    )


def test_INVARIANT_every_git_source_has_rev_field() -> None:
    """
    INV-DEP-1: Every [tool.uv.sources] entry using a git source MUST carry rev=.
    BREAKS: `uv tool install --reinstall` silently advances to HEAD on cerebro deploy.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    assert sources, "No [tool.uv.sources] section found — the torch index route is expected"

    git_sources = {
        name: entry
        for name, entry in sources.items()
        if isinstance(entry, dict) and "git" in entry
    }

    missing_rev = [name for name, entry in git_sources.items() if "rev" not in entry]

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


def test_SC10_openai_dependency_carries_an_explicit_floor_version() -> None:
    """
    SC-10: openai must be pinned to an explicit floor version in [project.dependencies]
    -- it is a plain PyPI dependency (not a git source), so INV-DEP-1's rev= check does
    not reach it, and a bare "openai" with no specifier would resolve to whatever is
    newest on install day.
    BREAKS: A breaking openai SDK release resolves silently into a deploy with no
    prismis-side commit to blame -- the same failure mode INV-DEP-1 guards for git
    sources, applied to the registry dependency this migration introduces.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    dependencies = data.get("project", {}).get("dependencies", [])
    openai_deps = [dep for dep in dependencies if dep.split(">=")[0].split("=")[0].split("<")[0].strip() == "openai"]

    assert openai_deps, "openai not found in [project.dependencies]"
    assert len(openai_deps) == 1, f"Expected exactly one openai entry, got {openai_deps}"
    assert any(op in openai_deps[0] for op in (">=", "==", "~=")), (
        f"SC-10 FAILED — openai dependency has no version specifier: {openai_deps[0]!r}"
    )


def test_SC3_key_library_is_gone_from_pyproject_and_lockfile() -> None:
    """
    SC-3: the retired git-only key library is neither a dependency, a uv source, nor a
    package stanza in uv.lock.
    BREAKS: a leftover declaration keeps a git dependency installable (and resolvable
    from a host with no network path to it) that no code imports.
    """
    retired = "apiconf"
    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    assert retired not in sources, f"{retired} still in [tool.uv.sources]"
    dependencies = data.get("project", {}).get("dependencies", [])
    assert not any(dep.split(">=")[0].strip() == retired for dep in dependencies)

    assert f'name = "{retired}"' not in _LOCKFILE.read_text(), (
        f"uv.lock still has a {retired} stanza; run `uv lock` from daemon/"
    )


def test_SC10_lockfile_has_no_llm_core_stanza() -> None:
    """
    SC-10 / SC-8: daemon/uv.lock must carry no llm-core package stanza after `uv lock`
    is re-run against the trimmed pyproject.toml.
    BREAKS: A stale llm-core entry in the lockfile keeps the git dependency resolvable
    and installable even though pyproject.toml no longer declares it.
    """
    assert _LOCKFILE.exists(), f"uv.lock not found: {_LOCKFILE}"

    content = _LOCKFILE.read_text()
    assert 'name = "llm-core"' not in content, (
        "SC-10 FAILED — uv.lock still contains an llm-core package stanza. "
        "Run `uv lock` from daemon/ to regenerate the lockfile."
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
