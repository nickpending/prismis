"""The daemon's fatal and verify paths print config errors with their brackets intact.

Invariant protected:
  - the startup path's 'Fatal error: {e}' and verify's 'config: {e}' lines render the
    section name in 'Add [api] section' and 'Config [llm] section outdated', and a value
    carrying a closing tag renders verbatim instead of raising MarkupError

Rich reads `[api]` in a printed string as a style tag and drops it. These tests run the
real typer app against a real config.toml and read what the real console wrote.
"""

import re

from typer.testing import CliRunner

from prismis_daemon.__main__ import app
from prismis_daemon.config import default_config_path
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD

_API_BLOCK = re.compile(r"^\[api\]\n(?:(?!\[).*\n)*", re.MULTILINE)
_LIGHT_SERVICE = 'light_service = "openrouter"\n'

runner = CliRunner()


def _write_config(text: str) -> None:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    (path.parent / "context.md").write_text(DEFAULT_CONTEXT_MD)


def _default_config() -> str:
    return DEFAULT_CONFIG_TOML.format(api_key="prismis-test-key")


def _without_api_section() -> str:
    text = _API_BLOCK.sub("", _default_config())
    assert "[api]" not in text, "the fixture must drop the whole [api] section"
    return text


def _outdated_llm_section() -> str:
    text = _default_config()
    assert _LIGHT_SERVICE in text
    return text.replace(_LIGHT_SERVICE, "")


def _bad_bracket_service() -> str:
    """A config whose loader error names a service carrying a closing tag."""
    return _default_config() + '\n[services."bad [/red] value"]\nadapter = "openai"\n'


def _flat(output: str) -> str:
    """The output with Rich's line wrapping collapsed, so a phrase is found across a wrap."""
    return " ".join(output.split())


def _startup() -> str:
    result = runner.invoke(app, [])
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    return _flat(result.output)


def _verify() -> str:
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    return _flat(result.output)


def test_startup_fatal_line_keeps_the_api_section_name() -> None:
    """
    INVARIANT: the startup 'Fatal error' line shows 'Add [api] section'
    BREAKS: the fix hint reads 'Add  section to config.toml' and names nothing
    """
    _write_config(_without_api_section())

    output = _startup()

    assert "Fatal error: API key not configured. Add [api] section" in output


def test_verify_config_line_keeps_the_api_section_name() -> None:
    """
    INVARIANT: verify's 'config:' line shows 'Add [api] section'
    BREAKS: the fix hint reads 'Add  section to config.toml' and names nothing
    """
    _write_config(_without_api_section())

    output = _verify()

    assert "config: API key not configured. Add [api] section" in output


def test_startup_fatal_line_keeps_the_outdated_llm_section_name() -> None:
    """
    INVARIANT: the startup 'Fatal error' line shows 'Config [llm] section outdated'
    BREAKS: the migrate hint reads 'Config  section outdated'
    """
    _write_config(_outdated_llm_section())

    output = _startup()

    assert "Fatal error: Config [llm] section outdated" in output


def test_verify_config_line_keeps_the_outdated_llm_section_name() -> None:
    """
    INVARIANT: verify's 'config:' line shows 'Config [llm] section outdated'
    BREAKS: the migrate hint reads 'Config  section outdated'
    """
    _write_config(_outdated_llm_section())

    output = _verify()

    assert "config: Config [llm] section outdated" in output


def test_startup_fatal_line_renders_a_closing_tag_verbatim() -> None:
    """
    INVARIANT: a loader error carrying '[/red]' prints verbatim on the startup path
    BREAKS: Rich raises MarkupError inside the error handler and the operator sees a
            traceback instead of the config error
    """
    _write_config(_bad_bracket_service())

    output = _startup()

    assert 'Fatal error: service "bad [/red] value" missing "base_url" field' in output


def test_verify_config_line_renders_a_closing_tag_verbatim() -> None:
    """
    INVARIANT: a loader error carrying '[/red]' prints verbatim on verify's config line
    BREAKS: Rich raises MarkupError inside the error handler and the operator sees a
            traceback instead of the config error
    """
    _write_config(_bad_bracket_service())

    output = _verify()

    assert 'config: service "bad [/red] value" missing "base_url" field' in output
