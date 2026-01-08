"""Global remote URL state for CLI."""

_remote_url: str | None = None


def set_remote_url(url: str | None) -> None:
    """Set the remote daemon URL."""
    global _remote_url
    _remote_url = url


def get_remote_url() -> str:
    """Get the remote daemon URL, defaulting to localhost."""
    return _remote_url or "http://localhost:8989"
