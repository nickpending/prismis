"""`prismis-cli context bootstrap` prints the one prompt, client-only, and cannot drift.

Success criteria covered: SC-1 (client-only, exact stdout), SC-2 (sections and question
count against the daemon's REQUIRED_CONTEXT_SECTIONS).
"""

import re
import subprocess
import sys
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from cli.__main__ import app
from cli.context import (
    BOOTSTRAP_PROMPT,
    CONTEXT_EXAMPLE_BEGIN,
    CONTEXT_EXAMPLE_END,
    OUTPUT_SPEC_MARKER,
)
from prismis_daemon.context_auto_updater import REQUIRED_CONTEXT_SECTIONS

CLI_SRC = Path(__file__).resolve().parents[2] / "src"

# Run in a fresh interpreter with prismis_daemon made unimportable and every socket
# connection refused. cli.__main__ puts daemon/src on sys.path at import, so the block
# is a meta-path finder that raises for the package, not the absence of a path.
_CLIENT_ONLY_SCRIPT = textwrap.dedent(
    """
    import socket
    import sys

    class _Block:
        def find_spec(self, name, path=None, target=None):
            if name == "prismis_daemon" or name.startswith("prismis_daemon."):
                raise ImportError("prismis_daemon blocked: client-only run")
            return None

    sys.meta_path.insert(0, _Block())

    def _no_network(*args, **kwargs):
        raise OSError("network blocked: client-only run")

    socket.socket.connect = _no_network
    socket.create_connection = _no_network
    socket.getaddrinfo = _no_network

    sys.argv = ["prismis-cli", "context", "bootstrap"]
    from cli.__main__ import main

    try:
        main()
    finally:
        sys.stderr.write("daemon_imported=%s\\n" % any(
            m == "prismis_daemon" or m.startswith("prismis_daemon.") for m in sys.modules
        ))
    """
)


def _question_count(prompt: str) -> int:
    """Count numbered interview questions: lines `Q<n>.` before the output spec."""
    interview = prompt.split(OUTPUT_SPEC_MARKER, 1)[0]
    return len(re.findall(r"^Q\d+\. ", interview, flags=re.MULTILINE))


def _output_spec(prompt: str) -> str:
    return prompt.split(OUTPUT_SPEC_MARKER, 1)[1]


def _missing_sections(prompt: str) -> list[str]:
    spec = _output_spec(prompt)
    return [
        s for s in (*REQUIRED_CONTEXT_SECTIONS, "## Not Interested") if s not in spec
    ]


def test_bootstrap_prints_exactly_the_prompt() -> None:
    result = CliRunner().invoke(app, ["context", "bootstrap"])

    assert result.exit_code == 0
    assert result.stdout == BOOTSTRAP_PROMPT


def test_bootstrap_works_with_daemon_unimportable_and_network_blocked() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CLIENT_ONLY_SCRIPT],
        capture_output=True,
        text=True,
        cwd=CLI_SRC,
        env={"PYTHONPATH": str(CLI_SRC), "PATH": ""},
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == BOOTSTRAP_PROMPT
    assert "daemon_imported=False" in proc.stderr


def test_prompt_output_spec_names_every_required_section() -> None:
    assert _missing_sections(BOOTSTRAP_PROMPT) == []


def test_section_drift_check_goes_red_when_a_section_is_removed() -> None:
    for section in (*REQUIRED_CONTEXT_SECTIONS, "## Not Interested"):
        broken = BOOTSTRAP_PROMPT.replace(section, "")
        assert _missing_sections(broken) == [section]


def test_prompt_asks_between_six_and_ten_questions() -> None:
    assert 6 <= _question_count(BOOTSTRAP_PROMPT) <= 10


def test_question_counter_ignores_other_numbered_text() -> None:
    padded = BOOTSTRAP_PROMPT + "\n1. a list item\n2. another\nQ99. after the spec\n"
    assert _question_count(padded) == _question_count(BOOTSTRAP_PROMPT)
    assert _question_count("OUTPUT:\n") == 0
    many = "\n".join(f"Q{i}. ask" for i in range(1, 12)) + "\nOUTPUT:\n"
    assert _question_count(many) == 11


def test_prompt_example_block_is_delimited_once() -> None:
    assert BOOTSTRAP_PROMPT.count(CONTEXT_EXAMPLE_BEGIN) == 1
    assert BOOTSTRAP_PROMPT.count(CONTEXT_EXAMPLE_END) == 1


def test_prompt_asks_for_source_add_lines_in_accepted_forms() -> None:
    spec = _output_spec(BOOTSTRAP_PROMPT)
    assert "prismis-cli source add <url>" in spec
    assert "reddit://" in spec
    assert "youtube.com" in spec
    assert "~/.config/prismis/context.md" in spec
