"""Tests for task 3.2: litellm-to-llm-core consumer migration invariants.

Covers:
- INV-001: Zero litellm imports in daemon/src/prismis_daemon/
- SC-11: Summarizer uses llm_core.complete() via service_name constructor
- SC-12: Evaluator uses llm_core.complete() via service_name constructor
- SC-13: Zero litellm references in daemon/src/ and pyproject.toml
- SC-15: migrate_config creates services.toml, apiconf, pricing.toml, updates config
- SC-16: __main__.py passes config.llm_service to consumer constructors
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

# Add src to path for absolute imports (mirrors conftest.py pattern)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.summarizer import ContentSummarizer

# --- INV-001 / SC-13 ---


def test_INVARIANT_zero_litellm_imports_in_source() -> None:
    """
    INV-001: Zero litellm imports must exist in daemon/src/prismis_daemon/
    BREAKS: Supply chain compromise - litellm v1.82.7/1.82.8 contained a credential stealer
    """
    src_dir = Path(__file__).parent.parent.parent / "src" / "prismis_daemon"
    assert src_dir.exists(), f"Source directory not found: {src_dir}"

    violations = []
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            if "import litellm" in line or "from litellm" in line:
                violations.append(f"{py_file.name}:{i}: {line.strip()}")

    assert violations == [], (
        "INV-001 FAILED - litellm imports found in source:\n" + "\n".join(violations)
    )


def test_INVARIANT_zero_litellm_in_pyproject() -> None:
    """
    SC-13: litellm must not appear in pyproject.toml dependencies
    BREAKS: Compromised package re-added as dependency silently
    """
    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found: {pyproject_path}"

    content = pyproject_path.read_text()
    assert "litellm" not in content, "SC-13 FAILED - litellm found in pyproject.toml"


# --- SC-11: Summarizer uses llm_core ---


def test_SC11_summarizer_constructor_takes_service_name() -> None:
    """
    SC-11: ContentSummarizer constructor must accept service_name: str, not config dict
    BREAKS: Daemon fails to initialize summarizer; no content is summarized
    """
    summarizer = ContentSummarizer("prismis-openai")
    assert summarizer.service_name == "prismis-openai"
    # Old API had .model and .config attributes - must be gone
    assert not hasattr(summarizer, "model"), (
        "Summarizer still has old .model attribute (config dict API not removed)"
    )
    assert not hasattr(summarizer, "config"), (
        "Summarizer still has old .config attribute (config dict API not removed)"
    )


def test_SC11_summarizer_calls_llm_core_complete() -> None:
    """
    SC-11: summarize_with_analysis() must call llm_core.complete() with service param
    BREAKS: Summarizer bypasses llm-core, uses wrong provider or no auth
    """
    summarizer = ContentSummarizer("prismis-openai")

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = (
        '{"summary": "Test summary", "reading_summary": "# Test\\n\\nContent",'
        ' "alpha_insights": ["insight"], "patterns": ["pattern"],'
        ' "entities": ["ai"], "quotes": [], "tools": [], "urls": []}'
    )
    fake_result.tokens.input = 100
    fake_result.tokens.output = 50
    fake_result.cost = 0.001
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 500

    with (
        patch(
            "prismis_daemon.summarizer.complete"
        ) as mock_complete,  # claudex-guard: allow-mock
        patch(
            "prismis_daemon.summarizer.get_circuit_breaker"
        ) as mock_cb,  # claudex-guard: allow-mock
    ):
        mock_cb.return_value.check_can_proceed.return_value = True
        mock_complete.return_value = fake_result

        result = summarizer.summarize_with_analysis(
            content="Test article content about AI",
            title="AI Test",
            url="https://example.com",
            source_type="rss",
        )

        # Verify complete() was called with service= kwarg
        assert mock_complete.called, "llm_core.complete() was not called"
        call_kwargs = mock_complete.call_args.kwargs
        assert call_kwargs.get("service") == "prismis-openai", (
            f"complete() called with wrong service: {call_kwargs.get('service')}"
        )

        assert result is not None
        assert result.summary == "Test summary"


# --- SC-12: Evaluator uses llm_core ---


def test_SC12_evaluator_constructor_takes_service_name() -> None:
    """
    SC-12: ContentEvaluator constructor must accept service_name: str, not config dict
    BREAKS: Daemon fails to initialize evaluator; no content is prioritized
    """
    evaluator = ContentEvaluator("prismis-openai")
    assert evaluator.service_name == "prismis-openai"
    assert not hasattr(evaluator, "model"), (
        "Evaluator still has old .model attribute (config dict API not removed)"
    )
    assert not hasattr(evaluator, "config"), (
        "Evaluator still has old .config attribute (config dict API not removed)"
    )


def test_SC12_evaluator_calls_llm_core_complete() -> None:
    """
    SC-12: evaluate_content() must call llm_core.complete() with service param
    BREAKS: Evaluator bypasses llm-core, uses wrong provider, content never prioritized
    """
    evaluator = ContentEvaluator("prismis-openai")

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = (
        '{"priority": "high", "matched_interests": ["AI"],'
        ' "reasoning": "Matches AI interest"}'
    )
    fake_result.tokens.input = 80
    fake_result.tokens.output = 30
    fake_result.cost = 0.0005
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 300

    with (
        patch(
            "prismis_daemon.evaluator.complete"
        ) as mock_complete,  # claudex-guard: allow-mock
        patch(
            "prismis_daemon.evaluator.get_circuit_breaker"
        ) as mock_cb,  # claudex-guard: allow-mock
    ):
        mock_cb.return_value.check_can_proceed.return_value = True
        mock_complete.return_value = fake_result

        result = evaluator.evaluate_content(
            content="AI article content",
            title="AI Test",
            url="https://example.com",
            context="High Priority: AI, machine learning",
        )

        assert mock_complete.called, "llm_core.complete() was not called"
        call_kwargs = mock_complete.call_args.kwargs
        assert call_kwargs.get("service") == "prismis-openai", (
            f"complete() called with wrong service: {call_kwargs.get('service')}"
        )

        assert result is not None
        assert result.priority is not None


# --- SC-15: migrate_config command ---

_OLD_FORMAT_CONFIG_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4.1-mini"
api_key = "sk-test-key-1234"

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"
max_comments = 100

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false

[archival.windows]
high_read = 30
medium_unread = 14
medium_read = 30
low_unread = 7
low_read = 30

[context]
auto_update_enabled = false
auto_update_interval_days = 7
auto_update_min_votes = 5
backup_count = 3
"""


def test_SC15_migrate_config_creates_services_and_updates_config() -> None:
    """
    SC-15: migrate_config must create services.toml, apiconf, pricing.toml, update config
    BREAKS: Cost tracking and LLM routing silently broken after migration
    """
    temp_dir = tempfile.mkdtemp()

    try:
        prismis_dir = Path(temp_dir) / "prismis"
        prismis_dir.mkdir(parents=True)
        config_toml = prismis_dir / "config.toml"
        config_toml.write_text(_OLD_FORMAT_CONFIG_TOML)

        with patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": temp_dir}
        ):  # claudex-guard: allow-mock
            from prismis_daemon.__main__ import migrate_config

            migrate_config()

        # Verify services.toml was created with correct content
        services_path = Path(temp_dir) / "llm-core" / "services.toml"
        assert services_path.exists(), "services.toml was not created"
        services_content = services_path.read_text()
        assert "prismis-openai" in services_content
        assert 'adapter = "openai"' in services_content
        assert 'default_model = "gpt-4.1-mini"' in services_content

        # Verify apiconf was created with the resolved API key
        apiconf_path = Path(temp_dir) / "apiconf" / "config.toml"
        assert apiconf_path.exists(), "apiconf/config.toml was not created"
        apiconf_content = apiconf_path.read_text()
        assert "[keys.openai]" in apiconf_content
        assert "sk-test-key-1234" in apiconf_content

        # Verify pricing.toml was created with model entries
        pricing_path = Path(temp_dir) / "llm-core" / "pricing.toml"
        assert pricing_path.exists(), "pricing.toml was not created"
        assert "gpt-4.1-mini" in pricing_path.read_text()

        # Verify config.toml [llm] section was updated to service= format
        updated_config = config_toml.read_text()
        assert 'service = "prismis-openai"' in updated_config, (
            "Config [llm] section was not updated to service= format"
        )
        assert "provider" not in updated_config, (
            "Old 'provider' key still in config after migration"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_SC15_migrate_config_is_idempotent() -> None:
    """
    SC-15: Running migrate_config must not overwrite pre-existing files
    BREAKS: User-customized services.toml or pricing.toml silently overwritten
    """
    temp_dir = tempfile.mkdtemp()

    try:
        prismis_dir = Path(temp_dir) / "prismis"
        prismis_dir.mkdir(parents=True)
        config_toml = prismis_dir / "config.toml"
        config_toml.write_text(_OLD_FORMAT_CONFIG_TOML)

        # Create pre-existing services.toml with custom content
        llm_core_dir = Path(temp_dir) / "llm-core"
        llm_core_dir.mkdir(parents=True)
        services_path = llm_core_dir / "services.toml"
        custom_services = 'default_service = "my-custom-service"\n'
        services_path.write_text(custom_services)

        with patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": temp_dir}
        ):  # claudex-guard: allow-mock
            from prismis_daemon.__main__ import migrate_config

            migrate_config()

        # Pre-existing services.toml must NOT be overwritten
        assert services_path.read_text() == custom_services, (
            "Existing services.toml was overwritten (idempotency violated)"
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# --- SC-16: __main__.py passes config.llm_service to consumers ---


def test_SC16_main_passes_llm_service_to_constructors() -> None:
    """
    SC-16: __main__.py must pass config.llm_service to ContentSummarizer and ContentEvaluator
    BREAKS: Consumers initialized with wrong service name, silently use wrong LLM
    """
    main_path = (
        Path(__file__).parent.parent.parent / "src" / "prismis_daemon" / "__main__.py"
    )
    assert main_path.exists(), f"__main__.py not found: {main_path}"

    source = main_path.read_text()

    assert "ContentSummarizer(config.llm_service)" in source, (
        "ContentSummarizer not constructed with config.llm_service"
    )

    assert "ContentEvaluator(config.llm_service)" in source, (
        "ContentEvaluator not constructed with config.llm_service"
    )
