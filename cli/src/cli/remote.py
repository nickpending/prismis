"""Global remote URL state for CLI."""

import os
from pathlib import Path

import tomllib

_remote_url: str | None = None
_remote_key: str | None = None


def set_remote_url(url: str | None) -> None:
    """Set the remote daemon URL (from --remote flag)."""
    global _remote_url
    _remote_url = url


def _load_remote_config() -> tuple[str | None, str | None]:
    """Load [remote] config from config.toml if present.

    Returns:
        Tuple of (url, key) or (None, None) if there is no config.toml or it has no
        [remote] section.

    Raises:
        RuntimeError: If config.toml exists but cannot be read or parsed. Falling back
            to localhost with no key would send the command to the wrong daemon.
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    config_path = Path(xdg_config_home) / "prismis" / "config.toml"

    if not config_path.exists():
        return None, None

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(f"Failed to read {config_path}: {e}") from e

    remote = config.get("remote", {})
    if not isinstance(remote, dict):
        raise RuntimeError(f"Invalid {config_path}: [remote] must be a table")
    return remote.get("url"), remote.get("key")


def get_remote_url() -> str:
    """Get the remote daemon URL.

    Priority: --remote flag > config.toml [remote].url > localhost
    """
    if _remote_url:
        return _remote_url

    url, _ = _load_remote_config()
    if url:
        return url

    return "http://localhost:8989"


def get_remote_key() -> str | None:
    """Get the remote API key from config.toml [remote].key.

    Returns:
        API key for remote daemon, or None if not configured.
    """
    _, key = _load_remote_config()
    return key


def is_remote_mode() -> bool:
    """Check if using remote mode (via flag or config)."""
    if _remote_url:
        return True
    url, _ = _load_remote_config()
    return url is not None
