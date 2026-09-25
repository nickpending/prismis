"""Tests for task 2.5: Dependency pinning invariants.

Covers:
- INV-DEP-1: Every [tool.uv.sources] git source MUST carry a rev= field
- INV-DEP-1 (format): rev= must be a commit hash, not a branch or tag name
- INV-DEP-1 (lockfile): uv.lock must contain the pinned rev in its apiconf stanza
- SC-10 (openai-sdk-migration): with llm-core gone, this file's assertions state the
  openai and apiconf pins explicitly instead of checking nothing -- apiconf's git
  source carries the validated rev "6fd244a", openai is pinned to a floor version on
  PyPI (not a git source, so INV-DEP-1 does not apply to it), and uv.lock reflects
  both.

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

# apiconf's validated rev (daemon/pyproject.toml's [tool.uv.sources] entry, carried
# over unchanged by the openai-sdk-migration work order -- only llm-core's own git
# source was removed).
_APICONF_VALIDATED_REV = "6fd244a"
_APICONF_VALIDATED_SHA = "6fd244a5041ac714cb21c48e41cf46b8b4fbba76"

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
    assert sources, "No [tool.uv.sources] section found — section expected"

    git_sources = {
        name: entry
        for name, entry in sources.items()
        if isinstance(entry, dict) and "git" in entry
    }
    assert git_sources, "No git sources found in [tool.uv.sources] — apiconf expected"

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


def test_SC10_apiconf_source_pin_matches_validated_rev() -> None:
    """
    SC-10: apiconf source entry must carry rev= "6fd244a" (validated commit) -- stated
    explicitly now that llm-core's own pin no longer occupies this file.
    BREAKS: A different rev would replace the validated apiconf build with an unknown
    version that has not been verified.
    """
    assert _PYPROJECT.exists(), f"pyproject.toml not found: {_PYPROJECT}"

    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    assert "apiconf" in sources, "apiconf not found in [tool.uv.sources]"

    entry = sources["apiconf"]
    assert isinstance(entry, dict), f"Unexpected apiconf source shape: {entry!r}"
    assert "rev" in entry, "SC-10 FAILED — apiconf source entry has no rev= field"

    rev = entry["rev"]
    assert _APICONF_VALIDATED_SHA.startswith(rev) or rev == _APICONF_VALIDATED_SHA, (
        f"SC-10 FAILED — apiconf rev= {rev!r} does not match validated commit "
        f"{_APICONF_VALIDATED_REV!r} (full SHA: {_APICONF_VALIDATED_SHA})"
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


def test_SC10_lockfile_contains_apiconf_pinned_rev() -> None:
    """
    SC-10: daemon/uv.lock must contain the pinned rev in the apiconf stanza.
    BREAKS: If lockfile drifts from pyproject.toml, `uv lock --check` exits non-zero
    and deploy tooling may re-resolve from a stale or HEAD ref.
    """
    assert _LOCKFILE.exists(), f"uv.lock not found: {_LOCKFILE}"

    content = _LOCKFILE.read_text()

    # uv expands the short rev to full SHA in the lockfile URL.
    # Both the short rev (in the ?rev= query param) and the full SHA
    # (in the fragment #SHA) must be present in the apiconf stanza.
    assert _APICONF_VALIDATED_REV in content, (
        f"SC-10 FAILED — pinned rev {_APICONF_VALIDATED_REV!r} not found in uv.lock. "
        "Run `uv lock` from daemon/ to regenerate the lockfile."
    )
    assert _APICONF_VALIDATED_SHA in content, (
        f"SC-10 FAILED — full SHA {_APICONF_VALIDATED_SHA!r} not found in uv.lock. "
        "Lockfile may have been regenerated against a different rev."
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
