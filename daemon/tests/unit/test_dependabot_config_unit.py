"""Tests for pinned-deps-update-path SC-4: the Dependabot configuration.

Covers:
- INV-DEPBOT-1: one monthly multi-ecosystem group, with the github-actions (`/`) and
  gomod (`/tui`) updates both opted into it
- INV-DEPBOT-2: the gomod allow list names the two `tool` modules and keeps
  `dependency-type: direct`, never `all`

Why these tests:
  staticcheck and gitleaks are `tool` directives in tui/go.mod, which Go records as
  `// indirect`. Dependabot's gomod updater skips indirect modules unless they are
  allow-listed by name, so dropping a name silently stops updates for that tool while
  the file still parses. `dependency-type: all` would instead open a PR for every
  indirect module each month. Nothing else reads this file before it is merged.
"""

from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEPENDABOT = _PROJECT_ROOT / ".github" / "dependabot.yml"

_TOOL_MODULES = {"honnef.co/go/tools", "github.com/zricethezav/gitleaks/v8"}


def _load() -> dict:
    assert _DEPENDABOT.exists(), f"dependabot.yml not found: {_DEPENDABOT}"
    data = yaml.safe_load(_DEPENDABOT.read_text())
    assert isinstance(data, dict)
    return data


def _updates(data: dict) -> dict:
    return {u["package-ecosystem"]: u for u in data["updates"]}


def test_INVARIANT_one_monthly_group_with_both_ecosystems_opted_in() -> None:
    """
    BREAKS: An update entry outside the group (or a group on another schedule) splits
    the monthly pull request, or leaves an ecosystem on Dependabot's default cadence.
    """
    data = _load()
    assert data["version"] == 2

    groups = data["multi-ecosystem-groups"]
    assert len(groups) == 1, f"expected exactly one group, got {list(groups)}"
    ((name, group),) = groups.items()
    assert group["schedule"]["interval"] == "monthly"

    updates = _updates(data)
    assert set(updates) == {"github-actions", "gomod"}
    assert updates["github-actions"]["directory"] == "/"
    assert updates["gomod"]["directory"] == "/tui"
    for ecosystem, update in updates.items():
        assert update["multi-ecosystem-group"] == name, (
            f"{ecosystem} is not opted into group {name!r}"
        )


def test_INVARIANT_gomod_allow_list_tracks_tool_modules_without_opening_indirects() -> None:
    """
    BREAKS: A missing dependency-name leaves that tool pinned forever with no PR; a
    switch to dependency-type all raises a PR for every indirect module.
    """
    allow = _updates(_load())["gomod"]["allow"]

    assert {"dependency-type": "direct"} in allow
    assert {"dependency-type": "all"} not in allow
    names = {entry["dependency-name"] for entry in allow if "dependency-name" in entry}
    assert names == _TOOL_MODULES
