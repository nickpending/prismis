"""check_context_md and the context analyzer read context.md through one walk.

Success criteria covered: SC-3 (check_context_md on a missing section, a fence line and
the prompt's output shape), C5 of vote-carveouts (one walk in one module; the analyzer's
topic lists equal what the regex parser it replaced returned, check_context_md's problems
and counts equal what the earlier line walk returned).
"""

import importlib.util
import os
import re
from pathlib import Path
from typing import Any

import pytest

from prismis_daemon.context_analyzer import ContextAnalyzer
from prismis_daemon.context_parser import (
    CONTEXT_COUNTED_SECTIONS,
    REQUIRED_CONTEXT_SECTIONS,
    check_context_md,
    parse_context,
)
from conftest import LOCAL_LIGHT_SERVICE, configure_local_services

_CONTEXT_PY = Path(__file__).resolve().parents[3] / "cli" / "src" / "cli" / "context.py"

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


def test_required_sections_are_the_three_the_validator_always_demanded() -> None:
    assert REQUIRED_CONTEXT_SECTIONS == (
        "## High Priority Topics",
        "## Medium Priority Topics",
        "## Low Priority Topics",
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


# --- C5: one walk, equal to the two parsers it replaced -------------------------------


def _regex_topics(context_text: str) -> dict[str, list[str]]:
    """The analyzer's regex parser as it stood before the shared walk: the reference."""
    sections: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for key, name in (
        ("high", "High"),
        ("medium", "Medium"),
        ("low", "Low"),
    ):
        match = re.search(
            rf"## {name} Priority Topics\s*\n(.*?)(?=\n## |\Z)",
            context_text,
            re.DOTALL,
        )
        if match:
            for line in match.group(1).split("\n"):
                line = line.strip()
                if line.startswith("- ") and line[2:].strip():
                    sections[key].append(line[2:].strip())
    return sections


def _line_walk_check(content: str) -> tuple[list[str], dict[str, int]]:
    """check_context_md as it stood before the shared walk: the reference."""
    problems = [
        f"missing required section: {section}"
        for section in REQUIRED_CONTEXT_SECTIONS
        if section not in content
    ]
    counts = dict.fromkeys(CONTEXT_COUNTED_SECTIONS, 0)
    current: str | None = None
    for number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            problems.append(f"code fence on line {number}: {stripped}")
        elif stripped.startswith("#"):
            if len(stripped) - len(stripped.lstrip("#")) <= 2:
                current = stripped if stripped in counts else None
        elif current is not None and stripped.startswith(("- ", "* ")):
            counts[current] += 1
    return problems, counts


_DIRECT_BULLET = (
    "## High Priority Topics\n- directly under the section\n"
    "### Group\n- under a group\n"
    "## Medium Priority Topics\n- medium one\n"
    "## Low Priority Topics\n"
)
_FENCED = "```markdown\n" + _GOOD + "```\n"
_MISSING_LOW = _GOOD.replace("## Low Priority Topics", "")
_FENCE_INSIDE = (
    "## High Priority Topics\n- one\n```\n- inside the fence\n```\n"
    "## Medium Priority Topics\n## Low Priority Topics\n"
)
_MIXED_MARKERS = (
    "## High Priority Topics\n- dash\n* star\n-\n- \n  - indented\n"
    "## Medium Priority Topics\n## Low Priority Topics\n"
)
_TEXTS = {
    "well-formed": _GOOD,
    "grouped": _GROUPED,
    "bullet-directly-under-section": _DIRECT_BULLET,
    "code-fence": _FENCED,
    "fence-inside-section": _FENCE_INSIDE,
    "missing-required-section": _MISSING_LOW,
    "mixed-markers": _MIXED_MARKERS,
    "empty": "",
}


@pytest.mark.parametrize("text", _TEXTS.values(), ids=_TEXTS.keys())
def test_analyzer_topics_equal_what_the_regex_parser_returned(text: str) -> None:
    assert parse_context(text).topics() == _regex_topics(text)


@pytest.mark.parametrize("text", _TEXTS.values(), ids=_TEXTS.keys())
def test_check_context_md_problems_and_counts_are_unchanged(text: str) -> None:
    assert check_context_md(text) == _line_walk_check(text)


def test_the_analyzer_reads_a_grouped_context_through_the_shared_walk(
    local_pipeline_stub: str, stub_requests: list[dict[str, Any]]
) -> None:
    """Entered through the analyzer's public method, over the local LLM stub: the
    bullets inside ### groups reach the request the stub received."""
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)

    ContextAnalyzer(LOCAL_LIGHT_SERVICE).analyze_flagged_items(
        [{"title": "t", "summary": "s"}], _GROUPED
    )

    assert len(stub_requests) == 1
    prompt = " ".join(m["content"] for m in stub_requests[0]["messages"])
    assert "LLM-driven vulnerability discovery" in prompt
    assert "Recon automation" in prompt


def test_the_old_per_module_parsers_are_gone() -> None:
    assert not hasattr(ContextAnalyzer, "_parse_context_sections")
    assert importlib.util.find_spec("prismis_daemon.context_auto_updater") is None
