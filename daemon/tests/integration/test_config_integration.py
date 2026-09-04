"""Integration tests for the config bootstrap workflow.

The previous contents of this file tested a config API that no longer exists: dict-style
`load_config()["daemon"][...]`, the `max_items` / `llm_provider` / `llm_model` /
`llm_api_key` fields removed by the light_service rename (decisions.md:223), and a
"missing or partial files fall back to defaults" contract that config.py:188-198 replaced
with a hard raise. Field-level loading is covered in tests/unit/test_config_unit.py; what
survives here is the one thing that is genuinely an integration: ensure_config() writes a
config that Config.from_file() can then load.
"""

import shutil
import tempfile
from pathlib import Path

from prismis_daemon.config import Config
from prismis_daemon.defaults import ensure_config


def test_ensure_config_produces_a_loadable_config(monkeypatch) -> None:
    """
    INVARIANT: the config ensure_config() writes on a fresh install is one from_file()
    can load and validate.
    BREAKS: a fresh install starts, writes its own default config, and then refuses to
    read it — the daemon cannot boot at all.
    """
    temp_home = tempfile.mkdtemp()

    try:
        monkeypatch.setenv("XDG_CONFIG_HOME", temp_home)

        # Fresh install: no config exists yet, so ensure_config reports it created one.
        config_existed = ensure_config()
        assert config_existed is False, "Should report a fresh config was created"

        config_dir = Path(temp_home) / "prismis"
        assert (config_dir / "config.toml").exists()
        assert (config_dir / "context.md").exists()

        # The written config loads and passes validation (from_file calls validate()).
        config_obj = Config.from_file(config_dir / "config.toml")

        assert config_obj.fetch_interval == 30
        assert config_obj.max_items_rss == 25
        assert config_obj.max_days_lookback == 30
        assert config_obj.llm_light_service == "prismis-openai"
        assert config_obj.high_priority_only is True
        assert config_obj.notification_command == "terminal-notifier"

        # A generated api_key is present and non-empty — validate() requires it.
        assert config_obj.api_key
        assert config_obj.api_key.startswith("prismis-")

        # Context comes from the shipped default.
        assert "High Priority Topics" in config_obj.context
        assert "AI/LLM breakthroughs" in config_obj.context

    finally:
        shutil.rmtree(temp_home, ignore_errors=True)


def test_ensure_config_is_idempotent(monkeypatch) -> None:
    """
    INVARIANT: ensure_config() never overwrites an existing config.
    BREAKS: every daemon start would reset the user's settings and rotate their API key,
    breaking every configured client.
    """
    temp_home = tempfile.mkdtemp()

    try:
        monkeypatch.setenv("XDG_CONFIG_HOME", temp_home)

        ensure_config()
        config_path = Path(temp_home) / "prismis" / "config.toml"
        first = config_path.read_text()

        # Second call must report the config already existed and leave it byte-identical.
        config_existed = ensure_config()
        assert config_existed is True
        assert config_path.read_text() == first

    finally:
        shutil.rmtree(temp_home, ignore_errors=True)
