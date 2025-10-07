"""Configuration loading from TOML and context.md files."""

import tomllib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    max_days_lookback: int

    # LLM settings
    llm_provider: str  # openai, ollama, anthropic, groq
    llm_model: str
    llm_api_key: str

    # Reddit settings
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str

    # Notification settings
    high_priority_only: bool
    notification_command: str

    # API settings
    api_key: str
    api_host: str

    # Context content
    context: str

    # Optional fields with defaults must come last
    llm_api_base: Optional[str] = None  # For Ollama and custom endpoints

    def get_max_items(self, source_type: str) -> int:
        """Get max items limit for a specific source type.

        Args:
            source_type: Type of source (rss, reddit, youtube)

        Returns:
            Max items limit for that source type
        """
        if source_type == "rss":
            return self.max_items_rss
        elif source_type == "reddit":
            return self.max_items_reddit
        elif source_type == "youtube":
            return self.max_items_youtube
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

        # Warn if Reddit credentials are not set (don't fail validation)
        if self.reddit_client_id.startswith(
            "env:"
        ) or self.reddit_client_secret.startswith("env:"):
            print(
                "Warning: Reddit credentials not configured. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables to use Reddit sources."
            )

    @classmethod
    def from_file(cls, config_path: Optional[Path] = None) -> "Config":
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
            raise ValueError(f"Failed to parse config file {config_path}: {e}")

        # Extract daemon settings
        daemon = config_dict.get("daemon", {})
        llm = config_dict.get("llm", {})
        reddit = config_dict.get("reddit", {})
        notifications = config_dict.get("notifications", {})
        api = config_dict.get("api", {})

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

        # Handle environment variable expansion
        api_key = expand_env_var(llm.get("api_key", "env:OPENAI_API_KEY"))
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
                max_days_lookback=daemon["max_days_lookback"],
                llm_provider=llm["provider"],
                llm_model=llm["model"],
                llm_api_key=api_key,
                llm_api_base=llm.get("api_base"),  # Optional for Ollama/custom
                reddit_client_id=reddit_client_id,
                reddit_client_secret=reddit_client_secret,
                reddit_user_agent=reddit["user_agent"],
                high_priority_only=notifications["high_priority_only"],
                notification_command=notifications["command"],
                api_key=api.get("key"),  # No default - must be explicitly set
                api_host=api.get(
                    "host", "127.0.0.1"
                ),  # Default to localhost for security
                context=context_content,
            )
        except KeyError as e:
            raise ValueError(f"Missing required config field: {e}")

        # Validate the loaded config
        config.validate()

        return config
