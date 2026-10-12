"""Pin the committed formulas as release templates.

The tap is the one home of real release values; prismis carries v0.0.0 placeholders so a
release never leaves a stale url, hash or version behind in this repo.
"""

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
FORMULAS = ("prismis-tui", "prismis-cli", "prismis-daemon")

TEMPLATE_URL = "https://github.com/nickpending/prismis/archive/refs/tags/v0.0.0.tar.gz"
TEMPLATE_SHA = "0" * 64
TEMPLATE_VERSION = "0.0.0"


def top_level(text: str, field: str) -> list[str]:
    return re.findall(rf'^  {field} "(.*)"$', text, re.M)


@pytest.mark.parametrize("name", FORMULAS)
def test_formula_carries_only_the_template_release_values(name: str) -> None:
    text = (HERE / f"{name}.rb").read_text()
    assert top_level(text, "url") == [TEMPLATE_URL]
    assert top_level(text, "sha256") == [TEMPLATE_SHA]
    assert top_level(text, "version") == [TEMPLATE_VERSION]
