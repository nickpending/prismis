"""The context.md validator and the saved-file check share REQUIRED_CONTEXT_SECTIONS.

Success criteria covered: SC-3 (check_context_md on a missing section, a fence line and
the prompt's output shape), SC-4 (the auto-updater validates against the constant).
"""

import importlib.util
from pathlib import Path

import pytest

from prismis_daemon import context_auto_updater
from prismis_daemon.context_auto_updater import (
    REQUIRED_CONTEXT_SECTIONS,
    ContextAutoUpdater,
    check_context_md,
)
from prismis_daemon.storage import Storage

from conftest import make_config

_CONTEXT_PY = (
    Path(__file__).resolve().parents[3] / "cli" / "src" / "cli" / "context.py"
)

_GOOD = """# Context

## High Priority Topics
- Local LLM inference breakthroughs and quantization techniques
- Rust systems programming and performance work

## Medium Priority Topics
- SQLite extensions and database internals

## Low Priority Topics
- General programming tutorials

## Not Interested
- Crypto, blockchain and web3
- Celebrity and entertainment news
"""


def _prompt_example_block() -> str:
    """The example context.md embedded in the CLI's bootstrap prompt, read from source."""
    spec = importlib.util.spec_from_file_location("bootstrap_context", _CONTEXT_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prompt = module.BOOTSTRAP_PROMPT
    start = prompt.index(module.CONTEXT_EXAMPLE_BEGIN) + len(
        module.CONTEXT_EXAMPLE_BEGIN
    )
    return prompt[start : prompt.index(module.CONTEXT_EXAMPLE_END)].strip() + "\n"


@pytest.fixture
def updater(test_db: Path) -> ContextAutoUpdater:
    return ContextAutoUpdater(make_config(), Storage(test_db))


def test_required_sections_are_the_three_the_validator_always_demanded() -> None:
    assert REQUIRED_CONTEXT_SECTIONS == (
        "## High Priority Topics",
        "## Medium Priority Topics",
        "## Low Priority Topics",
    )


def test_validator_accepts_a_well_formed_context(updater: ContextAutoUpdater) -> None:
    assert updater._validate_context_md(_GOOD) == (True, "Valid")


@pytest.mark.parametrize("section", REQUIRED_CONTEXT_SECTIONS)
def test_validator_names_each_missing_required_section(
    updater: ContextAutoUpdater, section: str
) -> None:
    assert updater._validate_context_md(_GOOD.replace(section, "")) == (
        False,
        f"Missing required section: {section}",
    )


def test_validator_still_rejects_short_bulletless_and_fenced_content(
    updater: ContextAutoUpdater,
) -> None:
    headings = "\n".join(REQUIRED_CONTEXT_SECTIONS)
    assert updater._validate_context_md(headings) == (
        False,
        f"Content too short ({len(headings)} chars)",
    )
    padded = headings + "\n" + "x" * 100
    assert updater._validate_context_md(padded) == (False, "No bullet points found")
    assert updater._validate_context_md(_GOOD + "```\n") == (
        False,
        "Contains code blocks (should be pure markdown)",
    )


def test_validator_follows_the_constant(
    updater: ContextAutoUpdater, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A section dropped from the constant stops being required; one added is enforced."""
    dropped = REQUIRED_CONTEXT_SECTIONS[1:]
    monkeypatch.setattr(context_auto_updater, "REQUIRED_CONTEXT_SECTIONS", dropped)
    assert updater._validate_context_md(_GOOD.replace(REQUIRED_CONTEXT_SECTIONS[0], ""))[0]

    monkeypatch.setattr(
        context_auto_updater,
        "REQUIRED_CONTEXT_SECTIONS",
        (*REQUIRED_CONTEXT_SECTIONS, "## Extra Section"),
    )
    assert updater._validate_context_md(_GOOD) == (
        False,
        "Missing required section: ## Extra Section",
    )


def test_check_names_a_missing_medium_section() -> None:
    problems, _ = check_context_md(_GOOD.replace("## Medium Priority Topics", ""))

    assert problems == ["missing required section: ## Medium Priority Topics"]


def test_check_reports_a_fence_line_with_its_number() -> None:
    fenced = "```markdown\n" + _GOOD + "```\n"

    problems, _ = check_context_md(fenced)

    assert problems == [
        "code fence on line 1: ```markdown",
        f"code fence on line {fenced.count(chr(10))}: ```",
    ]


def test_check_passes_the_prompts_output_shape_with_its_counts() -> None:
    problems, counts = check_context_md(_prompt_example_block())

    assert problems == []
    assert counts == {
        "## High Priority Topics": 2,
        "## Medium Priority Topics": 2,
        "## Low Priority Topics": 2,
        "## Not Interested": 2,
    }


def test_check_counts_only_bullets_under_each_heading() -> None:
    _, counts = check_context_md(_GOOD)

    assert counts["## High Priority Topics"] == 2
    assert counts["## Medium Priority Topics"] == 1
    assert counts["## Not Interested"] == 2


# The shape the operator's own context.md has: each section split into ### groups, with
# the bullets under the groups. A subheading belongs to its section; only a level-2
# heading starts a new one.
_GROUPED = """# Personal Context

## High Priority Topics

### AI-Powered Security
- LLM-driven vulnerability discovery
- Prompt injection research

### OSINT
- Recon automation

## Medium Priority Topics

### Security Engineering
- Threat modeling frameworks

## Low Priority Topics

### Industry Trends
- Compliance news

## Not Interested

### Basic Security
- Password tips
"""


def test_check_counts_bullets_under_subheadings_in_their_section() -> None:
    problems, counts = check_context_md(_GROUPED)

    assert problems == []
    assert counts == {
        "## High Priority Topics": 3,
        "## Medium Priority Topics": 1,
        "## Low Priority Topics": 1,
        "## Not Interested": 1,
    }


def test_a_new_level_two_heading_ends_the_section() -> None:
    _, counts = check_context_md(
        "## High Priority Topics\n- one\n## Unrelated Notes\n- not a topic\n"
        "## Medium Priority Topics\n## Low Priority Topics\n## Not Interested\n"
    )

    assert counts["## High Priority Topics"] == 1
