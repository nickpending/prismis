"""Tests for `prismis-daemon migrate-config`: moving an install's LLM services into
config.toml's [services.*] tables.

Real files in a real config home, the real command function, nothing patched. The
retired shared service file and key store are written the way an existing install has
them, key values included, so "no key value is copied" is asserted against real secrets
sitting right next to the config being migrated.

Covers:
- SC-4: a cerebro-shaped config home (kind service on the decisions adapter included)
  gains exactly the named services, with env:OPENROUTER_API_KEY, a timestamped backup,
  its comments intact, no secret in any written file, and a second run that changes
  nothing.
- SC-5: the pre-llm-core [llm] format migrates to a prismis-<provider> table without
  writing the retired files or copying the key value.
"""

import tomllib
from pathlib import Path

import pytest

from prismis_daemon.__main__ import migrate_config
from prismis_daemon.config import Config

# Distinct, greppable key values: if any of these reaches a file the command wrote, a
# test below names it.
SECRETS = (
    "sk-or-SECRET-LIGHT",
    "sk-or-SECRET-DEEP",
    "sk-or-SECRET-KIND",
    "sk-test-key-1234",
)

_TAIL = """
[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"
max_comments = 5

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

# keep-me: a comment the migration must not lose
[archival]
enabled = false
[archival.windows]
high_read = 999
medium_unread = 30
medium_read = 14
low_unread = 14
low_read = 7

[context]
auto_update_enabled = false
auto_update_interval_days = 30
auto_update_min_votes = 5
backup_count = 10
"""

_HEAD = """# keep-me: top-of-file comment
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 1
max_days_lookback = 30
"""

CEREBRO_CONFIG = (
    _HEAD
    + """
[llm]
# keep-me: which service summarizes
light_service = "prismis-light"
deep_service = "prismis-deep"
kind_service = "prismis-kind"
auto_extract = "high"
"""
    + _TAIL
)

RENAME_CONFIG = _HEAD + '\n[llm]\nservice = "prismis-light"\n' + _TAIL

PRE_LLM_CORE_CONFIG = (
    _HEAD
    + '\n[llm]\nprovider = "openai"\nmodel = "gpt-4.1-mini"\napi_key = "sk-test-key-1234"\n'
    + _TAIL
)

SERVICES_TOML = """default_service = "prismis-light"

[services.prismis-light]
adapter = "openai"
key = "prismis-light-key"
base_url = "https://openrouter.ai/api/v1"
default_model = "openai/gpt-5.4-nano"
app_title = "prismis"
app_url = "https://example.com/prismis"

[services.prismis-deep]
adapter = "openai"
key = "prismis-deep-key"
base_url = "https://openrouter.ai/api/v1"
default_model = "openai/gpt-5.4"

[services.prismis-kind]
adapter = "decisions"
key = "prismis-kind-key"
base_url = "https://openrouter.ai/api/alpha/decisions"
default_model = "typesafe/jev-1.13"

[services.other-app]
adapter = "openai"
key = "other-key"
base_url = "https://api.openai.com/v1"
default_model = "gpt-4.1-mini"
"""

KEY_STORE_TOML = """[keys.prismis-light-key]
provider = "openrouter"
value = "sk-or-SECRET-LIGHT"

[keys.prismis-deep-key]
provider = "openrouter"
value = "sk-or-SECRET-DEEP"

[keys.prismis-kind-key]
provider = "openrouter"
value = "sk-or-SECRET-KIND"
"""


def _seed(
    home: Path, config_text: str, services: str | None = None, keys: str | None = None
) -> Path:
    """Lay out a config home. The retired directories are named here, as an existing
    install has them: the shared service file under llm-core, the key store beside it."""
    prismis_dir = home / "prismis"
    prismis_dir.mkdir(parents=True)
    config_path = prismis_dir / "config.toml"
    config_path.write_text(config_text)
    (prismis_dir / "context.md").write_text("# Test context")
    if services is not None:
        (home / "llm-core").mkdir()
        (home / "llm-core" / "services.toml").write_text(services)
    if keys is not None:
        (home / ("api" + "conf")).mkdir()
        (home / ("api" + "conf") / "config.toml").write_text(keys)
    return config_path


def _snapshot(home: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(home)): p.read_bytes()
        for p in sorted(home.rglob("*"))
        if p.is_file()
    }


def _backups(config_path: Path) -> list[Path]:
    return sorted(config_path.parent.glob("config.toml.bak-*"))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_home = tmp_path / "confighome"
    cfg_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    return cfg_home


def test_cerebro_shaped_home_gains_the_three_services_and_a_second_run_changes_nothing(
    home: Path,
) -> None:
    """
    SC-4: every [llm]-named service becomes a [services.*] table equal to its source
    (default_model -> model), the key variable comes from the key's provider, and the
    second run is a byte-for-byte no-op.
    BREAKS: a table missing or altered, a key value leaked into config.toml or its
    backup, a lost comment, no backup, or a second run that appends or backs up again.
    """
    config_path = _seed(home, CEREBRO_CONFIG, SERVICES_TOML, KEY_STORE_TOML)
    services_before = (home / "llm-core" / "services.toml").read_bytes()
    keys_before = (home / ("api" + "conf") / "config.toml").read_bytes()

    migrate_config()

    migrated = tomllib.loads(config_path.read_text())["services"]
    source = tomllib.loads(SERVICES_TOML)["services"]
    assert set(migrated) == {"prismis-light", "prismis-deep", "prismis-kind"}, (
        "only the services [llm] names are migrated; other-app must stay behind"
    )
    for name, table in migrated.items():
        expected = {
            k: v for k, v in source[name].items() if k not in ("key", "default_model")
        }
        expected["model"] = source[name]["default_model"]
        expected["api_key"] = "env:OPENROUTER_API_KEY"
        assert table == expected, name

    text = config_path.read_text()
    assert "# keep-me: top-of-file comment" in text
    assert "# keep-me: which service summarizes" in text
    assert "# keep-me: a comment the migration must not lose" in text

    backups = _backups(config_path)
    assert len(backups) == 1, f"expected one timestamped backup, found {backups}"
    assert backups[0].read_text() == CEREBRO_CONFIG

    for path in home.rglob("*"):
        if path.is_file() and path.parent.name == "prismis":
            for secret in SECRETS:
                assert secret not in path.read_text(), (
                    f"{secret} leaked into {path.name}"
                )
    assert (home / "llm-core" / "services.toml").read_bytes() == services_before
    assert (home / ("api" + "conf") / "config.toml").read_bytes() == keys_before

    cfg = Config.from_file(config_path)
    assert cfg.llm_kind_service == "prismis-kind"
    assert cfg.services["prismis-kind"].adapter == "decisions"

    after_first = _snapshot(home)
    migrate_config()
    assert _snapshot(home) == after_first, "second run must change nothing"


def test_a_keyless_source_service_migrates_without_an_api_key(home: Path) -> None:
    """
    SC-4/SC-2 seam: key_required = false in the retired file becomes no api_key at all.
    BREAKS: a local-server service migrated with an env: reference to a variable nobody
    sets, so it fails resolution instead of calling keyless.
    """
    services = """[services.prismis-light]
adapter = "openai"
base_url = "http://localhost:11434/v1"
key_required = false
default_model = "llama3"
"""
    config_path = _seed(
        home,
        CEREBRO_CONFIG.replace("deep_service", "# deep_service").replace(
            "kind_service", "# kind_service"
        ),
        services,
    )

    migrate_config()

    table = tomllib.loads(config_path.read_text())["services"]["prismis-light"]
    assert table == {
        "adapter": "openai",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3",
    }


def test_unknown_source_fields_are_reported_not_silently_dropped(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    A field this schema does not carry is named in the output.
    BREAKS: a retired field vanishes during migration with no word to the operator.
    """
    services = SERVICES_TOML.replace(
        'app_title = "prismis"', 'app_title = "prismis"\ntimeout_seconds = 30'
    )
    _seed(home, CEREBRO_CONFIG, services, KEY_STORE_TOML)

    migrate_config()

    assert "timeout_seconds" in capsys.readouterr().out


def test_a_named_service_missing_from_the_retired_file_exits_nonzero_and_names_it(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    A service [llm] names that nothing can supply is an error, not a quiet success.
    BREAKS: the command reports "Migration complete" for a config whose verify then fails
    on an undefined service.
    """
    config_path = _seed(
        home,
        CEREBRO_CONFIG,
        SERVICES_TOML.split("[services.prismis-kind]")[0],
        KEY_STORE_TOML,
    )

    with pytest.raises(SystemExit) as exit_info:
        migrate_config()

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "prismis-kind" in out
    assert "Migration complete" not in out
    assert set(tomllib.loads(config_path.read_text())["services"]) == {
        "prismis-light",
        "prismis-deep",
    }


def test_the_rename_branch_renames_and_then_defines_the_service(home: Path) -> None:
    """
    A post-llm-core config (`service =`) gets light_service and its services table in
    one run, so it loads straight away.
    BREAKS: the rename lands but the service stays undefined, so the daemon still cannot
    resolve its light service.
    """
    config_path = _seed(home, RENAME_CONFIG, SERVICES_TOML, KEY_STORE_TOML)

    migrate_config()

    cfg = Config.from_file(config_path)
    assert cfg.llm_light_service == "prismis-light"
    assert cfg.services["prismis-light"].api_key == "env:OPENROUTER_API_KEY"
    assert "# keep-me: a comment the migration must not lose" in config_path.read_text()
    assert len(_backups(config_path)) == 1
    snapshot = _snapshot(home)
    migrate_config()
    assert _snapshot(home) == snapshot


def test_pre_llm_core_format_migrates_without_touching_the_retired_files_or_copying_the_key(
    home: Path,
) -> None:
    """
    SC-5: [llm] provider/model/api_key becomes [services.prismis-openai] with
    api_key = env:OPENAI_API_KEY; services.toml and the key store are never written
    and the literal key goes nowhere.
    BREAKS: the old path wrote the shared service file and copied the key into the key
    store; either one returning is the defect this migration exists to remove.
    """
    config_path = _seed(home, PRE_LLM_CORE_CONFIG)

    migrate_config()

    assert not (home / "llm-core").exists(), (
        "the retired service file must not be created"
    )
    assert not (home / ("api" + "conf")).exists(), "the key store must not be created"
    text = config_path.read_text()
    parsed = tomllib.loads(text)
    assert parsed["llm"] == {"light_service": "prismis-openai"}
    assert parsed["services"]["prismis-openai"] == {
        "adapter": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "api_key": "env:OPENAI_API_KEY",
    }
    # The backup is a verbatim copy of the user's own old file; every other file must
    # be free of the literal key.
    for path in home.rglob("*"):
        if path.is_file() and not path.name.startswith("config.toml.bak"):
            assert "sk-test-key-1234" not in path.read_text(), f"key copied into {path}"
    assert Config.from_file(config_path).llm_light_service == "prismis-openai"
    assert len(_backups(config_path)) == 1


def test_pre_llm_core_env_reference_keeps_the_variable_it_names(home: Path) -> None:
    """
    SC-5: an old `api_key = "env:MY_KEY"` already names the variable the user exports.
    BREAKS: the migration swaps in OPENAI_API_KEY and the user's exported variable is
    never read.
    """
    config_path = _seed(
        home,
        PRE_LLM_CORE_CONFIG.replace('"sk-test-key-1234"', '"env:MY_OPENAI_KEY"'),
    )

    migrate_config()

    table = tomllib.loads(config_path.read_text())["services"]["prismis-openai"]
    assert table["api_key"] == "env:MY_OPENAI_KEY"


def test_a_config_whose_services_are_all_defined_is_left_alone(home: Path) -> None:
    """
    Idempotency from the other side: a config that already defines what [llm] names
    gets no backup and no write.
    BREAKS: running migrate-config on a healthy install churns its config and piles up
    backups.
    """
    migrated = (
        CEREBRO_CONFIG
        + '\n[services.prismis-light]\nbase_url = "http://x/v1"\n'
        + '\n[services.prismis-deep]\nbase_url = "http://x/v1"\n'
        + '\n[services.prismis-kind]\nbase_url = "http://x/v1"\n'
    )
    _seed(home, migrated, SERVICES_TOML, KEY_STORE_TOML)
    before = _snapshot(home)

    migrate_config()

    assert _snapshot(home) == before


def test_success_line_names_the_table_it_added(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    The printed table header survives rich markup. An unescaped [services.x] reads as a
    style tag and is dropped, leaving the operator "Added  to <path>".
    BREAKS: the success line silently loses the name of what it added.
    """
    _seed(home, CEREBRO_CONFIG, SERVICES_TOML, KEY_STORE_TOML)

    migrate_config()

    output = " ".join(capsys.readouterr().out.split())
    assert "Added [services.prismis-light] to" in output, output
