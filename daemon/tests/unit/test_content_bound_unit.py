"""Unit tests for the article-text bound (bounded-llm-content SC-1, SC-2, SC-5).

Invariants protected:
- SC-1: no prompt the summarizer, evaluator or deep extractor builds carries more than
  MAX_CONTENT_BYTES UTF-8 bytes of article text or splits a character; a cut prompt
  carries the leading bytes followed by one line stating the cut and the sizes
- SC-2: content within the bound reaches each prompt unchanged, with no cut line
- SC-5: all three prompt builders go through the one `bound_content` helper in
  llm_call.py, and MAX_CONTENT_BYTES is defined once

Nothing is faked: the real prompt builders run on real strings.
"""

import re
from pathlib import Path

import pytest

import prismis_daemon
from prismis_daemon.deep_extractor import ContentDeepExtractor
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.llm_call import MAX_CONTENT_BYTES, bound_content
from prismis_daemon.summarizer import ContentSummarizer

_CUT_LINE = "\n\n[The article text above was cut"


def _prompts(content: str) -> dict[str, str]:
    return {
        "summarizer": ContentSummarizer("svc")._build_prompt(
            content, "T", "http://u", "rss", "", {}
        ),
        "evaluator": ContentEvaluator("svc")._build_evaluation_prompt(
            content, "T", "http://u", "ctx"
        )[1]["content"],
        "deep": ContentDeepExtractor("svc")._user_prompt(content, "T", "http://u"),
    }


def _article_part(prompt: str, content: str) -> str:
    """The article text a prompt carries: from where the content starts to the cut line."""
    start = prompt.index(content[:50])
    end = prompt.find(_CUT_LINE, start)
    return prompt[start : end if end != -1 else len(prompt)]


@pytest.mark.parametrize("char", ["─", "⠿", "😀"])
@pytest.mark.parametrize("builder", ["summarizer", "evaluator", "deep"])
def test_oversized_content_is_cut_on_a_character_boundary_and_says_so(
    builder: str, char: str
) -> None:
    """
    SC-1: 1.1M characters, half multi-byte, never put more than the bound in a prompt.
    BREAKS: a prompt carries the whole article (provider refuses it), or the cut lands
    inside a character (the model sees a replacement character, or the bound is broken).
    """
    content = char * 550_000 + "word " * 110_000
    assert len(content) > 1_000_000

    prompt = _prompts(content)[builder]

    part = _article_part(prompt, content)
    sent = part.encode()
    assert len(sent) <= MAX_CONTENT_BYTES
    assert len(sent) > MAX_CONTENT_BYTES - 4
    assert content.startswith(part), "cut keeps the article's leading text"
    assert "�" not in prompt
    total = len(content.encode())
    lines = [ln for ln in prompt.splitlines() if "was cut" in ln]
    assert len(lines) == 1
    assert f"{len(sent):,} of {total:,}" in lines[0]


@pytest.mark.parametrize("builder", ["summarizer", "evaluator", "deep"])
def test_ascii_content_over_the_bound_is_cut_to_exactly_the_bound(
    builder: str,
) -> None:
    """
    SC-1: 400,000 bytes of ASCII prose carries exactly 350,000 of them.
    BREAKS: a bound counted in characters-per-token rather than bytes.
    """
    content = "lorem ipsum dolor sit amet. " * 15_000
    content = content[:400_000]
    assert len(content.encode()) == 400_000

    part = _article_part(_prompts(content)[builder], content)

    assert part == content[:MAX_CONTENT_BYTES]


@pytest.mark.parametrize("builder", ["summarizer", "evaluator", "deep"])
def test_content_within_the_bound_reaches_the_prompt_unchanged(builder: str) -> None:
    """
    SC-2: a 2,000-byte article is in the prompt verbatim and no cut line appears.
    BREAKS: the helper reformatting or annotating content that fits.
    """
    content = "Plain article text. " * 100
    assert len(content.encode()) == 2_000

    prompt = _prompts(content)[builder]

    assert content in prompt
    assert "was cut" not in prompt


def test_content_exactly_at_the_bound_is_not_cut() -> None:
    """
    SC-1/SC-2 edge: MAX_CONTENT_BYTES bytes is within the bound, so no record.
    BREAKS: an off-by-one `<` that marks a fitting article as bounded.
    """
    bounded = bound_content("a" * MAX_CONTENT_BYTES)

    assert bounded.text == "a" * MAX_CONTENT_BYTES
    assert bounded.record is None


def test_the_record_carries_sent_and_total_bytes() -> None:
    """
    SC-3 input: the record the analysis stores names both sizes.
    BREAKS: a record that reports the prompt's length (note included) as sent_bytes.
    """
    content = "─" * 200_000

    record = bound_content(content).record

    assert record == {"sent_bytes": 349_998, "total_bytes": 600_000}


def test_every_prompt_builder_uses_the_one_helper() -> None:
    """
    SC-5: the three prompt builders interpolate bounded text and the bound is defined
    once, in llm_call.py.
    BREAKS: a fourth interpolation of raw `{content}` that bypasses the bound.
    """
    src = Path(prismis_daemon.__file__).parent
    for name in ("summarizer.py", "evaluator.py", "deep_extractor.py"):
        text = (src / name).read_text()
        assert "bound_content" in text, name
        assert "{content}" not in text, name
    definitions = [
        p.name
        for p in src.rglob("*.py")
        if re.search(r"^MAX_CONTENT_BYTES\s*=", p.read_text(), re.M)
    ]
    assert definitions == ["llm_call.py"]
