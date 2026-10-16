"""Unit tests for the [drift] threshold config key (C6).

Success criteria covered: C6.
"""

import tomllib
from pathlib import Path

import pytest

from prismis_daemon.config import Config
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML

from conftest import TEST_API_KEY


def _config_with(tmp_path: Path, drift_section: str) -> Path:
    """The shipped template with its [drift] section replaced by `drift_section`."""
    text = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    head = text[: text.index("[drift]")]
    path = tmp_path / "config.toml"
    path.write_text(head + drift_section)
    return path


def test_configured_threshold_is_used(tmp_path: Path) -> None:
    """
    INVARIANT: [drift] threshold = 0.35 loads as the drift threshold
    BREAKS: the check ignores the user's tuning and flags their quiet weekends
    """
    config = Config.from_file(_config_with(tmp_path, "[drift]\nthreshold = 0.35\n"))

    assert config.drift_threshold == 0.35


def test_threshold_defaults_to_0_20_with_no_drift_section(tmp_path: Path) -> None:
    """
    INVARIANT: a config.toml with no [drift] section checks at 0.20
    BREAKS: every config written before this change fails to load, or checks at zero
    """
    config = Config.from_file(_config_with(tmp_path, ""))

    assert config.drift_threshold == 0.20


@pytest.mark.parametrize("value", ["0", "0.0", "1", "1.0", "-0.1", "1.5", "20", "true"])
def test_threshold_outside_zero_one_is_refused_naming_the_key(
    tmp_path: Path, value: str
) -> None:
    """
    INVARIANT: a threshold of 0, 1 or beyond (or not a number) is refused at load, and
               the error names the [drift] threshold key
    BREAKS: a threshold of 20 (meant as percent) silently disables the check
    """
    path = _config_with(tmp_path, f"[drift]\nthreshold = {value}\n")

    with pytest.raises(ValueError, match=r"\[drift\] threshold"):
        Config.from_file(path)


def test_shipped_template_documents_the_key_and_its_default() -> None:
    """
    INVARIANT: the template written on first run has a [drift] section that names the
               threshold key and the 0.20 default, and still parses as TOML
    BREAKS: the key exists but nobody can find it
    """
    text = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)

    section = text[text.index("[drift]") :]

    assert "threshold = 0.20" in section
    assert tomllib.loads(text)["drift"] == {}
