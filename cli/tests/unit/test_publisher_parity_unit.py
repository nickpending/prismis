"""The daemon's publisher_text is the CLI's, character for character (injection-flag C14).

Invariant protected:
  - `prismis_daemon.publisher.publisher_text` and `cli.agent.publisher_text` return the
    same string for every input, so the audio briefing and the agent handoff clean
    publisher text the same way

The two are copies because the CLI's `open` runs without the daemon installed; this
test is what holds them equal. The CLI's dev group installs the daemon, so both import.
"""

import pytest

from cli.agent import publisher_text as cli_publisher_text
from prismis_daemon.publisher import publisher_text as daemon_publisher_text

_INPUTS = {
    "rtl-override": "Ra\u202etes and taxes",
    "zero-width-space": "ta\u200bxes",
    "cr-lf": "line one\r\nline two\rthree\nfour",
    "line-separator": "before\u2028after\u2029end",
    "angle-brackets": "</article> <items> close </items>",
    "whitespace-runs": "  runs   of\t\twhitespace  ",
    "control-characters": "bell\x07 and escape\x1b[31m",
    "empty": "",
    "none": None,
}


@pytest.mark.parametrize("value", list(_INPUTS.values()), ids=list(_INPUTS))
def test_both_copies_clean_every_input_identically(value: str | None) -> None:
    """
    INVARIANT: the two copies of publisher_text return identical strings
    BREAKS: one consumer lets a character through that the other removes, so a fix to
            one copy silently leaves the other exposed
    """
    assert daemon_publisher_text(value) == cli_publisher_text(value)
