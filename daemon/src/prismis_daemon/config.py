"""Configuration loading from TOML and context.md files."""

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .defaults import DEFAULT_CONTEXT_MD


@dataclass
class Config:
    """Configuration dataclass with validation.

    Loads from ~/.config/prismis/config.toml - config file is required.
    Provides validation for configuration values.
    """

    # Daemon settings
    fetch_interval: int
    max_items_rss: int
    max_items_reddit: int
    max_items_youtube: int
    max_items_file: int
    max_days_lookback: int

    # LLM settings
    llm_service: str  # Service name from ~/.config/llm-core/services.toml

    # Reddit settings
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str
    reddit_max_comments: int  # Max comments to fetch per post (0 = unlimited)

    # Notification settings
    high_priority_only: bool
    notification_command: str

    # API settings
    api_key: str
    api_host: str

    # Context content
    context: str

    # Archival settings (required fields, no defaults)
    archival_enabled: bool
    archival_high_read: int | None  # None = never archive HIGH
    archival_medium_unread: int
    archival_medium_read: int
    archival_low_unread: int
    archival_low_read: int

    # Context auto-update settings
    context_auto_update_enabled: bool
    context_auto_update_interval_days: int
    context_auto_update_min_votes: int
    context_backup_count: int

    # Optional fields with defaults must come last
    # Audio settings (uses lspeak for all TTS)
    audio_provider: str = "system"  # system (free, native TTS) or elevenlabs
    audio_voice: str | None = None  # Voice ID/name (provider-specific)

    def get_max_items(self, source_type: str) -> int:
        """Get max items limit for a specific source type.

        Args:
            source_type: Type of source (rss, reddit, youtube, file)

        Returns:
            Max items limit for that source type
        """
        if source_type == "rss":
            return self.max_items_rss
        elif source_type == "reddit":
            return self.max_items_reddit
        elif source_type == "youtube":
            return self.max_items_youtube
        elif source_type == "file":
            return self.max_items_file
        else:
            return 25  # Fallback default for unknown types

    def validate(self) -> None:
        """Validate configuration values.

        Raises:
            ValueError: If any configuration values are invalid.
        """
        # Validate API key is set
        if not self.api_key:
            raise ValueError(
                "API key not configured. Add [api] section to config.toml with key='your-random-key'"
            )

        # Validate max_items ranges
        for field_name, value in [
            ("max_items_rss", self.max_items_rss),
            ("max_items_reddit", self.max_items_reddit),
            ("max_items_youtube", self.max_items_youtube),
            ("max_items_file", self.max_items_file),
        ]:
            if not 1 <= value <= 100:
                raise ValueError(f"{field_name} must be between 1 and 100, got {value}")

        # Validate fetch_interval
        if self.fetch_interval < 1:
            raise ValueError(
                f"fetch_interval must be at least 1 minute, got {self.fetch_interval}"
            )

        # Validate max_days_lookback
        if not 1 <= self.max_days_lookback <= 365:
            raise ValueError(
                f"max_days_lookback must be between 1 and 365 days, got {self.max_days_lookback}"
            )

        # Validate reddit_max_comments (0 = unlimited is valid)
        if self.reddit_max_comments < 0:
            raise ValueError(
                f"reddit_max_comments must be >= 0 (0 means unlimited), got {self.reddit_max_comments}"
            )

        # Warn if Reddit credentials are not set (don't fail validation)
        if self.reddit_client_id.startswith(
            "env:"
        ) or self.reddit_client_secret.startswith("env:"):
            print(
                "Warning: Reddit credentials not configured. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables to use Reddit sources."
            )

        # Validate archival windows (must be positive or None)
        for field_name, value in [
            ("archival_high_read", self.archival_high_read),
            ("archival_medium_unread", self.archival_medium_unread),
            ("archival_medium_read", self.archival_medium_read),
            ("archival_low_unread", self.archival_low_unread),
            ("archival_low_read", self.archival_low_read),
        ]:
            if value is not None and value < 1:
                raise ValueError(
                    f"{field_name} must be positive (or None to disable), got {value}"
                )

        # Validate context auto-update settings
        if not 1 <= self.context_auto_update_interval_days <= 365:
            raise ValueError(
                f"context_auto_update_interval_days must be between 1 and 365, got {self.context_auto_update_interval_days}"
            )
        if not 1 <= self.context_auto_update_min_votes <= 1000:
            raise ValueError(
                f"context_auto_update_min_votes must be between 1 and 1000, got {self.context_auto_update_min_votes}"
            )
        if not 1 <= self.context_backup_count <= 100:
            raise ValueError(
                f"context_backup_count must be between 1 and 100, got {self.context_backup_count}"
            )

    @classmethod
    def from_file(cls, config_path: Path | None = None) -> "Config":
        """Load configuration from TOML file.

        Args:
            config_path: Optional path to config file.
                        Defaults to $XDG_CONFIG_HOME/prismis/config.toml
                        (or ~/.config/prismis/config.toml)

        Returns:
            Config instance with loaded values.
        """
        if config_path is None:
            # XDG Base Directory support
            xdg_config_home = os.environ.get(
                "XDG_CONFIG_HOME", str(Path.home() / ".config")
            )
            config_path = Path(xdg_config_home) / "prismis" / "config.toml"

        # Load TOML config - required to exist
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Run 'make install-config' to create default configuration, or create config.toml manually."
            )

        try:
            with open(config_path, "rb") as f:
                config_dict = tomllib.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse config file {config_path}: {e}") from e

        # Extract daemon settings
        daemon = config_dict.get("daemon", {})
        llm = config_dict.get("llm", {})
        reddit = config_dict.get("reddit", {})
        notifications = config_dict.get("notifications", {})
        api = config_dict.get("api", {})
        audio = config_dict.get("audio", {})
        archival = config_dict.get("archival", {})
        context_config = config_dict.get("context", {})

        # Load context markdown
        context_file = config_path.parent / "context.md"
        if context_file.exists():
            try:
                context_content = context_file.read_text()
            except Exception:
                context_content = DEFAULT_CONTEXT_MD
        else:
            context_content = DEFAULT_CONTEXT_MD

        # Helper function to expand environment variables
        def expand_env_var(value: str) -> str:
            if value.startswith("env:"):
                env_var = value[4:]
                return os.environ.get(env_var, value)
            return value

        # Detect old config format
        if "provider" in llm and "service" not in llm:
            raise ValueError(
                "Config format outdated: [llm] section uses 'provider'/'model'/'api_key' fields. "
                "Run 'prismis-daemon migrate-config' to upgrade your config.toml to the new format."
            )
        if "provider" in llm and "service" in llm:
            logging.getLogger(__name__).warning(
                "Config [llm] section has both 'provider' and 'service' — "
                "old 'provider' key is ignored. Remove it to silence this warning."
            )

        # Handle environment variable expansion
        reddit_client_id = expand_env_var(
            reddit.get("client_id", "env:REDDIT_CLIENT_ID")
        )
        reddit_client_secret = expand_env_var(
            reddit.get("client_secret", "env:REDDIT_CLIENT_SECRET")
        )

        # Create config instance - all fields required in config file
        try:
            config = cls(
                fetch_interval=daemon["fetch_interval"],
                max_items_rss=daemon["max_items_rss"],
                max_items_reddit=daemon["max_items_reddit"],
                max_items_youtube=daemon["max_items_youtube"],
                max_items_file=daemon["max_items_file"],
                max_days_lookback=daemon["max_days_lookback"],
                llm_service=llm["service"],
                reddit_client_id=reddit_client_id,
                reddit_client_secret=reddit_client_secret,
                reddit_user_agent=reddit["user_agent"],
                reddit_max_comments=reddit["max_comments"],
                high_priority_only=notifications["high_priority_only"],
                notification_command=notifications["command"],
                api_key=api.get("key"),  # No default - must be explicitly set
                api_host=api.get(
                    "host", "127.0.0.1"
                ),  # Default to localhost for security
                context=context_content,
                audio_provider=audio.get("provider", "macos"),
                audio_voice=audio.get("voice"),
                archival_enabled=archival["enabled"],
                archival_high_read=archival["windows"]["high_read"],
                archival_medium_unread=archival["windows"]["medium_unread"],
                archival_medium_read=archival["windows"]["medium_read"],
                archival_low_unread=archival["windows"]["low_unread"],
                archival_low_read=archival["windows"]["low_read"],
                context_auto_update_enabled=context_config.get(
                    "auto_update_enabled", True
                ),
                context_auto_update_interval_days=context_config.get(
                    "auto_update_interval_days", 30
                ),
                context_auto_update_min_votes=context_config.get(
                    "auto_update_min_votes", 5
                ),
                context_backup_count=context_config.get("backup_count", 10),
            )
        except KeyError as e:
            raise ValueError(f"Missing required config field: {e}") from e

        # Validate the loaded config
        config.validate()

        return config
