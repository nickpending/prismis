"""Integration tests for source management commands."""

import sys
from pathlib import Path
from typer.testing import CliRunner

# Add CLI src to path
cli_src = Path(__file__).parent.parent.parent / "src"
daemon_src = Path(__file__).parent.parent.parent.parent / "daemon" / "src"
sys.path.insert(0, str(cli_src))
sys.path.insert(0, str(daemon_src))

from cli.__main__ import app  # noqa: E402
from prismis_daemon.storage import Storage  # noqa: E402


def test_add_rss_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test adding an RSS feed source."""
    # Add RSS source
    result = cli_runner.invoke(app, ["source", "add", "https://example.com/feed.xml"])

    # Check command succeeded
    assert result.exit_code == 0
    assert "✅ Added rss source:" in result.stdout
    assert "Example" in result.stdout
    assert "URL: https://example.com/feed.xml" in result.stdout

    # Verify in database
    storage = Storage(mock_home_dir)
    sources = storage.get_all_sources()
    assert len(sources) == 1
    assert sources[0]["url"] == "https://example.com/feed.xml"
    assert sources[0]["type"] == "rss"
    assert sources[0]["name"] == "Example"


def test_add_reddit_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test adding a Reddit source with reddit:// scheme."""
    # Add Reddit source
    result = cli_runner.invoke(app, ["source", "add", "reddit://rust"])

    # Check command succeeded
    assert result.exit_code == 0
    assert "✅ Added reddit source:" in result.stdout
    assert "rust" in result.stdout  # Name extracted is just 'rust'
    assert "URL: https://www.reddit.com/r/rust.json" in result.stdout

    # Verify in database
    storage = Storage(mock_home_dir)
    sources = storage.get_all_sources()
    assert len(sources) == 1
    assert sources[0]["url"] == "https://www.reddit.com/r/rust.json"
    assert sources[0]["type"] == "reddit"
    assert sources[0]["name"] == "rust"  # Name is just 'rust' not 'r/rust'


def test_add_youtube_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test adding a YouTube source."""
    # Add YouTube source
    result = cli_runner.invoke(app, ["source", "add", "youtube://@mkbhd"])

    # Check command succeeded
    assert result.exit_code == 0
    assert "✅ Added youtube source:" in result.stdout
    assert "@mkbhd" in result.stdout

    # Verify in database
    storage = Storage(mock_home_dir)
    sources = storage.get_all_sources()
    assert len(sources) == 1
    assert sources[0]["url"] == "youtube://@mkbhd"
    assert sources[0]["type"] == "youtube"
    assert sources[0]["name"] == "@mkbhd"


def test_add_source_with_custom_name(
    mock_home_dir: Path, cli_runner: CliRunner
) -> None:
    """Test adding a source with a custom name."""
    # Add source with custom name
    result = cli_runner.invoke(
        app, ["source", "add", "https://example.com/feed", "--name", "My Custom Feed"]
    )

    # Check command succeeded
    assert result.exit_code == 0
    assert "✅ Added rss source:" in result.stdout
    assert "My Custom Feed" in result.stdout

    # Verify in database
    storage = Storage(mock_home_dir)
    sources = storage.get_all_sources()
    assert len(sources) == 1
    assert sources[0]["name"] == "My Custom Feed"


def test_list_sources_empty(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test listing sources when none exist."""
    result = cli_runner.invoke(app, ["source", "list"])

    assert result.exit_code == 0
    assert "No sources configured" in result.stdout


def test_list_sources_with_data(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test listing sources with multiple sources."""
    # Add some sources first
    storage = Storage(mock_home_dir)
    storage.add_source("https://example.com/feed.xml", "rss", "Example Feed")
    storage.add_source("https://reddit.com/r/python.json", "reddit", "r/python")

    # List sources
    result = cli_runner.invoke(app, ["source", "list"])

    # Check output
    assert result.exit_code == 0
    assert "Content Sources" in result.stdout
    assert "Exam" in result.stdout  # Table truncates to "Exam... Feed"
    assert "Feed" in result.stdout  # But shows on next line
    assert "r/py" in result.stdout  # Table truncates long names
    assert "rss" in result.stdout
    assert "redd" in result.stdout  # Type truncated to "redd..."
    assert "✅" in result.stdout  # Active status emoji
    assert "Yes" in result.stdout  # Active status text
    assert "Total sources: 2" in result.stdout


def test_remove_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test removing a source."""
    # Add a source first
    storage = Storage(mock_home_dir)
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Test Feed")

    # Remove it with --force to skip confirmation
    result = cli_runner.invoke(app, ["source", "remove", source_id, "--force"])

    # Check command succeeded
    assert result.exit_code == 0
    assert "✅ Removed source:" in result.stdout
    assert "Test Feed" in result.stdout

    # Verify removed from database
    sources = storage.get_all_sources()
    assert len(sources) == 0


def test_remove_nonexistent_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test removing a source that doesn't exist."""
    # Try to remove non-existent source
    fake_id = "00000000-0000-0000-0000-000000000000"
    result = cli_runner.invoke(app, ["source", "remove", fake_id, "--force"])

    # Should fail gracefully
    assert result.exit_code == 1
    assert "❌ Source not found:" in result.stdout


def test_pause_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test pausing a source."""
    # Add an active source
    storage = Storage(mock_home_dir)
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Test Feed")

    # Pause it
    result = cli_runner.invoke(app, ["source", "pause", source_id])

    # Check command succeeded
    assert result.exit_code == 0
    assert f"✅ Paused source: {source_id}" in result.stdout

    # Verify in database
    sources = storage.get_all_sources()
    assert len(sources) == 1
    assert sources[0]["active"] is False


def test_resume_source(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test resuming a paused source."""
    # Add a source and pause it
    storage = Storage(mock_home_dir)
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Test Feed")

    # Manually pause it in database
    from prismis_daemon.database import get_db_connection

    conn = get_db_connection(mock_home_dir)
    conn.execute("UPDATE sources SET active = 0 WHERE id = ?", (source_id,))
    conn.commit()
    conn.close()

    # Resume it
    result = cli_runner.invoke(app, ["source", "resume", source_id])

    # Check command succeeded
    assert result.exit_code == 0
    assert f"✅ Resumed source: {source_id}" in result.stdout

    # Verify in database
    sources = storage.get_all_sources()
    assert len(sources) == 1
    assert sources[0]["active"] is True
    assert sources[0]["error_count"] == 0  # Should reset error count


def test_source_type_detection(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test automatic source type detection from URLs."""
    test_cases = [
        ("https://blog.rust-lang.org/feed.xml", "rss"),
        ("https://reddit.com/r/programming", "reddit"),
        ("https://youtube.com/@veritasium", "youtube"),
        ("https://www.reddit.com/r/golang", "reddit"),
        ("https://youtube.com/channel/UCabc123", "youtube"),
    ]

    storage = Storage(mock_home_dir)

    for url, expected_type in test_cases:
        # Add source
        result = cli_runner.invoke(app, ["source", "add", url])
        assert result.exit_code == 0

        # Check type in database
        sources = storage.get_all_sources()
        last_source = sources[-1]  # Get most recently added
        assert last_source["type"] == expected_type, (
            f"URL {url} should be type {expected_type}"
        )


def test_duplicate_source_handling(mock_home_dir: Path, cli_runner: CliRunner) -> None:
    """Test that adding duplicate sources is handled gracefully."""
    # Add a source
    result1 = cli_runner.invoke(app, ["source", "add", "https://example.com/feed.xml"])
    assert result1.exit_code == 0

    # Try to add the same source again
    result2 = cli_runner.invoke(app, ["source", "add", "https://example.com/feed.xml"])
    assert result2.exit_code == 0  # Should succeed (returns existing ID)

    # Verify only one source in database
    storage = Storage(mock_home_dir)
    sources = storage.get_all_sources()
    assert len(sources) == 1
