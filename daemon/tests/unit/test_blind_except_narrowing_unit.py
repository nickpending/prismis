"""Each narrowed handler handles the failure it exists for, and only that one (gh #58).

Invariant protected:
  - a malformed date, a broken metadata line, an unreadable transcript, a refused Reddit
    call, an unparseable URL and an unopenable database each still produce the outcome
    they produced when the handler was `except Exception`
  - a failure outside the family the handler names is not swallowed: it reaches the
    boundary above it

Each test drives the real failure through the real code: a script that prints a broken
line stands in for yt-dlp (the way the other youtube tests do), a path that does not exist
stands in for a missing binary, and a stand-in praw submission raises the request error
the client raises.

Every narrowed handler, the exception types it catches, and the test that drives it. Each
was shown red by swapping its catch for an exception nothing raises. Tests named alone are
in this file; the rest are existing tests, under daemon/tests/ (cli/tests/ for the last).

  __main__.py validate_llm_config   ConfigError, openai.OpenAIError
      integration/test_llm_startup_validation_integration.py
      test_FAILURE_network_timeout_handling, test_FAILURE_provider_auth_failure_guidance
  __main__.py verify, config        ValueError, OSError
      unit/test_verify_subcommand_unit.py test_verify_config_failure_exits_1_immediately
  __main__.py verify, light/deep    ConfigError, openai.OpenAIError
      unit/test_verify_subcommand_unit.py test_verify_light_service_unreachable_exits_1,
      test_verify_deep_service_configured_but_unreachable_exits_1
  __main__.py verify, kind          ConfigError, ValueError, httpx.HTTPError
      unit/test_verify_subcommand_unit.py
      test_verify_kind_service_unreachable_counts_failure_and_exits_1
  __main__.py verify, sources       sqlite3.Error, OSError
      test_the_doctor_reports_a_sources_check_that_cannot_open_the_database
  llm_validator.py deep             ConfigError, openai.OpenAIError
      unit/test_dual_service_config_unit.py
      test_validate_llm_services_deep_failure_is_non_fatal
  llm_validator.py kind             ConfigError, ValueError, httpx.HTTPError
      unit/test_llm_startup_validation_unit.py test_INVARIANT_kind_service_failure_is_non_fatal
  api.py get_validator              ValueError, OSError
      integration/test_api_integration.py
      test_validator_dependency_degrades_when_config_will_not_load
  config.py context.md read         OSError, UnicodeDecodeError (re-raised as ValueError)
      unit/test_config_unit.py
      test_config_loading_with_unreadable_context_raises_naming_context_md,
      test_config_loading_with_non_utf8_context_raises_naming_context_md
  verify_chain.py config            ValueError, OSError
      test_verify_chain_reports_a_config_that_cannot_load
  validator.py rss                  httpx.InvalidURL
      test_an_rss_url_httpx_cannot_parse_is_a_validation_error_not_a_crash
  validator.py reddit probe         PrawcoreException, PRAWException, requests.RequestException,
                                    KeyError
      integration/test_validator_integration.py test_validate_source_routes_a_real_probe_failure
  validator.py youtube              ValueError
      test_a_youtube_url_urlparse_rejects_is_a_validation_error
  observability.py cleanup          OSError
      test_cleanup_skips_a_file_it_cannot_remove_and_says_so
  fetchers/rss.py published date    TypeError, ValueError, OverflowError
      unit/test_fetcher_unit.py test_parse_published_date_handles_invalid_dates
  fetchers/rss.py updated date      TypeError, ValueError, OverflowError
      test_rss_falls_back_from_a_bad_published_date_to_the_updated_one
  fetchers/reddit.py client init    PRAWException, PrawcoreException, requests.RequestException
      test_a_reddit_client_that_cannot_be_built_leaves_the_fetcher_without_one
  fetchers/reddit.py listing date   TypeError, ValueError, OverflowError, OSError
      test_a_post_with_an_unusable_date_is_still_fetched
  fetchers/reddit.py item date      TypeError, ValueError, OverflowError, OSError
      unit/test_reddit_fetcher_unit.py test_to_content_item_date_parsing_error
  fetchers/reddit.py comments       PRAWException, PrawcoreException, requests.RequestException
      test_a_failed_comment_fetch_is_recorded_on_the_item,
      test_a_defect_in_the_comment_fetch_is_not_swallowed
  fetchers/youtube.py metadata line ValueError, KeyError, TypeError
      test_discovery_skips_a_broken_metadata_line_and_keeps_the_rest
  fetchers/youtube.py transcript    OSError, UnicodeDecodeError
      test_transcript_extraction_reports_a_missing_binary_as_a_failed_fetch,
      test_transcript_extraction_reports_an_undecodable_transcript_as_a_failed_fetch
  cli remote.py _load_remote_config OSError, TOMLDecodeError, UnicodeDecodeError
      cli/tests/unit/test_remote_config_unit.py
"""

import logging
import sys
from pathlib import Path

import prawcore
import pytest
import requests
from rich.console import Console

from prismis_daemon.__main__ import verify
from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.fetchers.youtube import YouTubeFetcher
from prismis_daemon.observability import ObservabilityLogger
from prismis_daemon.validator import SourceValidator
from prismis_daemon.verify_chain import execute_chain

from conftest import make_config
from fixtures.reddit_mocks import create_link_post_mock


# --- fetchers/youtube.py ----------------------------------------------------------------


def _printing_yt_dlp(tmp_path: Path, lines: list[str]) -> list[str]:
    """A runnable stand-in for yt-dlp discovery that prints exactly `lines`."""
    script = tmp_path / "print_lines.py"
    script.write_text(
        "import sys\n"
        f"for line in {lines!r}:\n"
        "    print(line)\n"
    )
    return [sys.executable, str(script)]


def test_discovery_skips_a_broken_metadata_line_and_keeps_the_rest(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: one video line that is not valid metadata costs that video only
    BREAKS: a malformed line stops discovery for the whole channel, or the narrowed
            handler misses the JSON error and the channel's fetch fails
    """
    good = (
        '{"id": "a1", "title": "Good", "duration": 60, "upload_date": "20260101",'
        ' "view_count": 5, "webpage_url": "https://www.youtube.com/watch?v=a1"}'
    )
    not_json = "WARNING: not json at all"
    missing_title = '{"id": "b2"}'
    wrong_shape = "[1, 2, 3]"
    fetcher = YouTubeFetcher()
    fetcher.yt_dlp_cmd = _printing_yt_dlp(
        tmp_path, [not_json, good, missing_title, wrong_shape]
    )

    videos = fetcher._discover_channel_videos("https://www.youtube.com/@channel")

    assert [v["id"] for v in videos] == ["a1"]


def test_transcript_extraction_reports_a_missing_binary_as_a_failed_fetch() -> None:
    """
    INVARIANT: yt-dlp that cannot be started is a fetch_failed outcome naming the error
    BREAKS: the video is dropped as "empty", or the OSError escapes the extraction
    """
    fetcher = YouTubeFetcher()
    fetcher.yt_dlp_cmd = ["/nonexistent/yt-dlp"]

    result = fetcher._extract_transcript("https://www.youtube.com/watch?v=abc123")

    assert result.text is None
    assert result.outcome == "fetch_failed"
    assert result.detail == "FileNotFoundError"


def test_transcript_extraction_reports_an_undecodable_transcript_as_a_failed_fetch(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: a subtitle file that is not UTF-8 is a fetch_failed outcome, not a crash
    BREAKS: the UnicodeDecodeError escapes and the video fails in the caller's boundary
    """
    script = tmp_path / "write_bad_vtt.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "out = args[args.index('--output') + 1]\n"
        "vid = args[-1].split('v=')[1]\n"
        "open(out.replace('%(id)s', vid).replace('%(ext)s', 'en-orig.vtt'), 'wb')"
        ".write(b'WEBVTT\\n\\n\\xff\\xfe not utf-8 \\x80')\n"
    )
    fetcher = YouTubeFetcher()
    fetcher.yt_dlp_cmd = [sys.executable, str(script)]

    result = fetcher._extract_transcript("https://www.youtube.com/watch?v=abc123")

    assert result.outcome == "fetch_failed"
    assert result.detail == "UnicodeDecodeError"


def test_a_failure_outside_the_narrowed_family_is_not_swallowed_by_extraction() -> None:
    """
    INVARIANT: only OSError and UnicodeDecodeError are the extraction's expected failures
    BREAKS: the narrowing is cosmetic and a defect still reads as a failed fetch
    """
    fetcher = YouTubeFetcher()
    fetcher.yt_dlp_cmd = [None]  # type: ignore[list-item]

    with pytest.raises(TypeError):
        fetcher._extract_transcript("https://www.youtube.com/watch?v=abc123")


# --- fetchers/rss.py --------------------------------------------------------------------


class _Entry:
    def __init__(self, published=None, updated=None) -> None:
        self.published_parsed = published
        self.updated_parsed = updated


def test_rss_falls_back_from_a_bad_published_date_to_the_updated_one() -> None:
    """
    INVARIANT: a feed date tuple that is not a date is skipped, not raised
    BREAKS: a feed with one bad date tuple fails its entry
    """
    fetcher = RSSFetcher(config=make_config())
    bad = (2024, 13, 50, 25, 70, 80)
    good = (2024, 5, 6, 7, 8, 9)

    assert fetcher._parse_published_date(_Entry(published=bad, updated=good)) == (
        fetcher._parse_published_date(_Entry(published=good))
    )
    assert fetcher._parse_published_date(_Entry(published=bad, updated=bad)) is None
    assert fetcher._parse_published_date(_Entry(updated=bad)) is None


# --- fetchers/reddit.py -----------------------------------------------------------------


def test_a_reddit_client_that_cannot_be_built_leaves_the_fetcher_without_one(
    reddit_credentials: None,
) -> None:
    """
    INVARIANT: praw refusing its configuration is logged and leaves reddit None
    BREAKS: the PRAWException escapes the constructor and the whole daemon fails to build
    """
    config = make_config(
        reddit_client_id="id", reddit_client_secret="secret", reddit_user_agent=None
    )  # type: ignore[arg-type]

    fetcher = RedditFetcher(config=config)

    assert fetcher.reddit is None
    assert fetcher.credentials_missing is False


class _Subreddit:
    def __init__(self, posts: list) -> None:
        self.posts = posts

    def hot(self, limit: int) -> list:
        return self.posts


class _RedditClient:
    def __init__(self, posts: list) -> None:
        self.posts = posts

    def subreddit(self, name: str) -> _Subreddit:
        return _Subreddit(self.posts)


def test_a_post_with_an_unusable_date_is_still_fetched(reddit_credentials: None) -> None:
    """
    INVARIANT: a created_utc that is not a timestamp leaves the date empty, not the post lost
    BREAKS: the TypeError escapes the listing loop and the subreddit's fetch fails
    """
    post = create_link_post_mock(
        is_self=True, selftext="A post with a body", created_utc="not a timestamp"
    )
    post.stickied = False
    fetcher = RedditFetcher(config=make_config())
    fetcher.reddit = _RedditClient([post])  # type: ignore[assignment]

    items = fetcher.fetch_content({"url": "https://reddit.com/r/python", "id": "s1"})

    assert [i.content for i in items] == ["A post with a body"]
    assert items[0].published_at is None


def _comment_failure(error: Exception):
    post = create_link_post_mock(
        is_self=True, selftext="A post with a body", url="https://reddit.com/r/x/1"
    )
    post.comments.replace_more.side_effect = error
    return post


@pytest.mark.parametrize(
    "error",
    [
        prawcore.exceptions.RequestException(OSError("reset"), (), {}),
        requests.exceptions.ConnectionError("refused"),
    ],
    ids=["prawcore", "requests"],
)
def test_a_failed_comment_fetch_is_recorded_on_the_item(
    error: Exception, reddit_credentials: None
) -> None:
    """
    INVARIANT: a comment fetch the client refused is `comments_outcome` fetch_failed
    BREAKS: the failure returns [] and the item is indistinguishable from a quiet post
    """
    fetcher = RedditFetcher(config=make_config())

    item = fetcher._to_content_item(_comment_failure(error), "source-1")

    assert item.analysis is not None
    assert item.analysis["comments_outcome"] == {
        "outcome": "fetch_failed",
        "detail": type(error).__name__,
    }


def test_a_post_with_no_comments_carries_no_comments_outcome(
    reddit_credentials: None,
) -> None:
    """
    INVARIANT: a comment fetch that succeeded with zero comments stores no outcome key
    BREAKS: every quiet post is marked failed, or the two cases cannot be told apart
    """
    post = create_link_post_mock(is_self=True, selftext="A post with a body")
    fetcher = RedditFetcher(config=make_config())

    item = fetcher._to_content_item(post, "source-1")

    assert item.analysis is not None
    assert "comments_outcome" not in item.analysis


def test_a_defect_in_the_comment_fetch_is_not_swallowed(
    reddit_credentials: None,
) -> None:
    """
    INVARIANT: only the client's request errors are the comment fetch's expected failure
    BREAKS: the narrowing is cosmetic and any bug reads as "comments failed"
    """
    fetcher = RedditFetcher(config=make_config())

    with pytest.raises(RuntimeError, match="a defect"):
        fetcher._fetch_comments(_comment_failure(RuntimeError("a defect")))


# --- validator.py -----------------------------------------------------------------------


def test_an_rss_url_httpx_cannot_parse_is_a_validation_error_not_a_crash() -> None:
    """
    INVARIANT: a URL httpx rejects as invalid answers (False, "RSS validation error: ...")
    BREAKS: the InvalidURL escapes the feed validator's own handler
    """
    ok, message, metadata = SourceValidator()._validate_rss("http://example.com:abc/feed")

    assert ok is False
    assert message is not None and message.startswith("RSS validation error")
    assert metadata is None


def test_a_youtube_url_urlparse_rejects_is_a_validation_error() -> None:
    """
    INVARIANT: a URL urlparse cannot parse answers (False, "YouTube validation error: ...")
    BREAKS: the ValueError escapes the validator's own handler
    """
    ok, message, _ = SourceValidator()._validate_youtube("https://[::1")

    assert ok is False
    assert message is not None and message.startswith("YouTube validation error")


def test_the_validation_boundary_logs_a_defect_and_still_answers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: a failure no validator routed answers (False, "Validation failed: ...")
               and its traceback is logged
    BREAKS: the request dies, or the defect is reported with no traceback
    """
    validator = SourceValidator()

    with caplog.at_level(logging.WARNING):
        # A non-string URL is a defect in the caller, which no validator names.
        ok, message, _ = validator.validate_source(None, "rss")  # type: ignore[arg-type]

    assert ok is False
    assert message is not None and message.startswith("Validation failed")
    assert any(r.exc_info for r in caplog.records if r.levelno >= logging.WARNING)


# --- observability.py -------------------------------------------------------------------


def test_cleanup_skips_a_file_it_cannot_remove_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    INVARIANT: an old events file that cannot be unlinked is reported to stderr and skipped
    BREAKS: the OSError escapes cleanup and the daemon's startup retention pass fails
    """
    base = tmp_path / "observability"
    obs = ObservabilityLogger(base)
    old = base / "2020-01-01_events.jsonl"
    old.write_text("{}\n")
    base.chmod(0o500)
    try:
        removed = obs.cleanup_old_files(retention_days=30)
    finally:
        base.chmod(0o700)

    assert removed == 0
    assert "Error removing old file" in capsys.readouterr().err
    assert old.exists()


def test_the_event_writer_swallows_a_failed_write_and_logs_the_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: an event that cannot be written never raises into its caller, and the
               traceback is logged at ERROR
    BREAKS: logging an event takes down the pipeline step that emitted it, or the failure
            leaves one stderr line and no traceback
    """
    obs = ObservabilityLogger(tmp_path)

    with caplog.at_level(logging.WARNING):
        obs.log("test.event", unserializable=object())

    assert "Error logging event" in capsys.readouterr().err
    assert any(
        r.exc_info and isinstance(r.exc_info[1], TypeError)
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


# --- verify_chain.py and the doctor ---------------------------------------------------------


@pytest.mark.parametrize("config_text", [None, "[daemon\nnot = toml"], ids=["missing", "malformed"])
def test_verify_chain_reports_a_config_that_cannot_load(
    config_text: str | None, isolated_xdg_env: Path
) -> None:
    """
    INVARIANT: a config that is missing or malformed ends the chain with exit 1 and no links
    BREAKS: the FileNotFoundError or ValueError escapes the chain command as a traceback
    """
    config_path = isolated_xdg_env / "config.toml"
    if config_text is None:
        config_path.unlink()
    else:
        config_path.write_text(config_text)

    code, links = execute_chain(
        "https://example.com/feed.xml", "rss", False, Console(quiet=True)
    )

    assert (code, links) == (1, [])


def test_the_doctor_reports_a_sources_check_that_cannot_open_the_database(
    test_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    INVARIANT: a database sqlite cannot open fails the sources check, and verify goes on
    BREAKS: the sqlite3.Error escapes `verify` as a traceback instead of a failed check
    """
    test_db.write_bytes(b"this is not a sqlite database" * 100)

    with pytest.raises(SystemExit) as exited:
        verify()

    assert exited.value.code == 1
    assert "sources check failed" in capsys.readouterr().out
