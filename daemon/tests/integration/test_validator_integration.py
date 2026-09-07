"""Integration tests for SourceValidator - real network validation.

Some tests here are ungated and run in the gate. They are not exceptions to the rule
below: a socket this suite opens on loopback and controls for the length of one test is
not a third party, and a bound proven against one is proven, where the same bound read
back off the object that just set it is not.

Two gates guard the rest, and they are not the same gate. PRISMIS_LIVE_NETWORK_TESTS
covers everything here that reaches a third party. Reddit credentials are a second,
narrower requirement, and both variables are checked wherever they are needed: the
suite's XDG seal means credentials reach these tests only through the environment, so
setting one of the pair un-skips a test that then fails on a 401 and reads as a broken
subreddit rather than a half-set environment.

Refusing a credential and never having one are different answers, so the test that
proves the first needs no secret at all — garbage credentials earn a real 401 from
Reddit — while the tests that prove a subreddit's own state need working ones.
"""

import os
import time

import pytest

from prismis_daemon.validator import SourceValidator

from conftest import make_config

LIVE_NETWORK = pytest.mark.skipif(
    not os.environ.get("PRISMIS_LIVE_NETWORK_TESTS"),
    reason="Hits live third-party endpoints. Set PRISMIS_LIVE_NETWORK_TESTS=1 to run.",
)

LIVE_REDDIT = pytest.mark.skipif(
    not os.environ.get("PRISMIS_LIVE_NETWORK_TESTS")
    or not os.environ.get("REDDIT_CLIENT_ID")
    or not os.environ.get("REDDIT_CLIENT_SECRET"),
    reason="Hits Reddit's authenticated API. Set PRISMIS_LIVE_NETWORK_TESTS=1 and BOTH "
    "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to run.",
)


def reddit_validator() -> SourceValidator:
    """Build a validator carrying the credentials the environment supplies."""
    return SourceValidator(
        make_config(
            reddit_client_id=os.environ["REDDIT_CLIENT_ID"],
            reddit_client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        )
    )


@LIVE_NETWORK
def test_valid_sources_accepted() -> None:
    """
    INVARIANT: Known-good sources must always validate as true
    BREAKS: Users can't add sources they need
    """
    validator = SourceValidator()

    # Test well-known, stable RSS feed
    is_valid, error, _metadata = validator.validate_source(
        "https://simonwillison.net/atom/everything/", "rss"
    )
    assert is_valid is True, f"Simon Willison's feed should be valid: {error}"
    assert error is None, "Valid feed should have no error"

    # Test well-known YouTube channel formats
    youtube_urls = [
        "https://youtube.com/@mkbhd",
        "https://youtube.com/c/CGPGrey",
        "youtube://@veritasium",
    ]

    for url in youtube_urls:
        is_valid, error, _metadata = validator.validate_source(url, "youtube")
        assert is_valid is True, f"YouTube {url} should be valid: {error}"
        assert error is None, f"Valid YouTube URL should have no error: {url}"


@LIVE_NETWORK
def test_invalid_sources_rejected() -> None:
    """
    INVARIANT: Invalid sources must be rejected with clear errors
    BREAKS: Bad sources pollute the database
    """
    validator = SourceValidator()

    # Test non-existent domain
    is_valid, error, _metadata = validator.validate_source(
        "https://this-domain-definitely-does-not-exist-12345.com/feed.xml", "rss"
    )
    assert is_valid is False, "Non-existent domain should fail"
    assert error is not None, "Should have error message"
    assert "Network error" in error or "nodename" in error, (
        "Should explain network failure"
    )

    # Test invalid YouTube URL (video instead of channel)
    is_valid, error, _metadata = validator.validate_source(
        "https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube"
    )
    assert is_valid is False, "Video URL should fail"
    assert error is not None, "Should have error message"
    assert "not supported" in error or "channel" in error.lower(), (
        "Should explain need channel URL"
    )


@LIVE_NETWORK
def test_network_timeout_handling() -> None:
    """
    FAILURE MODE: Network timeouts must fail gracefully
    GRACEFUL: Clear error message, no hanging
    """
    validator = SourceValidator()

    # Override timeout to be very short to trigger timeout on slow endpoints
    validator.timeout = 0.001  # 1ms timeout - will timeout on any real network call

    # Test RSS timeout with a real endpoint that will be too slow
    is_valid, error, _metadata = validator.validate_source(
        "https://httpbin.org/delay/5",
        "rss",  # This endpoint delays 5 seconds
    )
    assert is_valid is False, "Timeout should fail validation"
    assert error is not None, "Should have error message"
    assert "timed out" in error.lower(), "Should mention timeout"

    # Reset timeout for other tests
    validator.timeout = 5.0


@LIVE_NETWORK
def test_malformed_rss_handling() -> None:
    """
    FAILURE MODE: Malformed RSS/XML must be rejected
    GRACEFUL: Clear error about invalid feed format
    """
    validator = SourceValidator()

    # Test with a real URL that returns HTML instead of RSS
    is_valid, error, _metadata = validator.validate_source(
        "https://google.com",
        "rss",  # Google homepage, not an RSS feed
    )
    assert is_valid is False, "HTML page should fail RSS validation"
    assert error is not None, "Should have error message"
    assert "invalid" in error.lower() or "format" in error.lower(), (
        "Should mention invalid format"
    )


def test_validate_reddit_rejects_an_unparseable_url() -> None:
    """
    INVARIANT: A URL naming no subreddit is refused before any client is built
    BREAKS: The parse failure reaches PRAW as a subreddit name and comes back as an
            unrelated API error
    NOTE: Ungated on purpose — it reaches nothing. It also calls the private reddit
          entry point directly, which is what holds the parse/probe/interpret split to
          the name the rest of this suite uses.
    """
    validator = SourceValidator()

    is_valid, error, metadata = validator._validate_reddit(
        "https://httpbin.org/status/429"
    )

    assert is_valid is False
    assert error == "Could not extract subreddit name from URL"
    assert metadata is None


@LIVE_NETWORK
def test_reddit_invalid_credentials_are_named() -> None:
    """
    INVARIANT: Credentials Reddit refuses are reported as credentials, not as a private
               or missing subreddit
    BREAKS: The operator is sent to Reddit's subreddit settings for a problem that lives
            in their own client id and secret
    NOTE: Needs no valid secret. Reddit answers syntactically well-formed garbage with a
          401, which is exactly the outcome under test.
    """
    validator = SourceValidator(
        make_config(
            reddit_client_id="prismis-not-a-real-client-id",
            reddit_client_secret="prismis-not-a-real-client-secret",
        )
    )

    is_valid, error, _metadata = validator.validate_source(
        "https://reddit.com/r/python", "reddit"
    )

    assert is_valid is False
    assert error is not None
    assert "credentials" in error.lower(), (
        f"A refused credential must say so, got: {error!r}"
    )
    assert "private" not in error.lower(), "Must not blame the subreddit"
    assert "not configured" not in error.lower(), (
        "Refused credentials and absent credentials are different answers"
    )
    assert "prismis-not-a-real" not in error, "No credential may reach the message"


@LIVE_REDDIT
def test_reddit_valid_subreddit_accepted_with_display_name() -> None:
    """
    INVARIANT: A real public subreddit validates and reports its prefixed display name
    BREAKS: Reddit sources cannot be added, or are added named from the URL instead of
            r/Name
    """
    is_valid, error, metadata = reddit_validator().validate_source(
        "https://reddit.com/r/python", "reddit"
    )

    assert is_valid is True, f"r/python should be valid: {error}"
    assert error is None, "Valid subreddit should have no error"
    assert metadata is not None
    assert metadata.get("display_name", "").lower() == "r/python", (
        f"Expected the prefixed display name, got: {metadata}"
    )


@LIVE_REDDIT
def test_reddit_missing_subreddit_rejected() -> None:
    """
    INVARIANT: A subreddit that does not exist is named as absent
    BREAKS: A typo in the subreddit name reads as a credential or access problem
    """
    is_valid, error, _metadata = reddit_validator().validate_source(
        "https://reddit.com/r/this_subreddit_definitely_does_not_exist_12345", "reddit"
    )

    assert is_valid is False, "Non-existent subreddit should fail"
    assert error is not None, "Should have error message"
    assert "does not exist" in error, f"Should explain the subreddit is absent: {error}"


@LIVE_REDDIT
def test_reddit_private_subreddit_handling() -> None:
    """
    FAILURE MODE: A subreddit that exists but refuses access returns 403
    GRACEFUL: The message says private or quarantined, not missing
    NOTE: r/lounge is restricted to Reddit Premium members. If that ever changes the
          test skips rather than asserting something it can no longer observe.
    """
    is_valid, error, _metadata = reddit_validator().validate_source(
        "https://reddit.com/r/lounge", "reddit"
    )

    if is_valid:
        pytest.skip("r/lounge is not private/restricted anymore")

    assert error is not None, "Should have error message"
    assert "private or quarantined" in error, (
        f"An inaccessible subreddit must be named as such: {error}"
    )


def test_validate_source_routes_a_real_probe_failure(
    hung_peer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: A probe that raises comes back out of the PUBLIC method as the routed
               message for its cause, not as the blind except's generic string
    BREAKS: The composition between the probe and the mapping is where every routed
            message is either delivered or swallowed. Proving the mapping in isolation
            says nothing about it — validate_source wraps the whole dispatch in a catch
            that turns any escape into one identical "Validation failed" for every cause
    NOTE: Ungated and third-party-free. The failure is real, not simulated: the probe
          opens a real socket to a listener this test owns, which accepts and then never
          answers, so prawcore raises its own RequestException the way a stalled peer
          makes it.
    """
    monkeypatch.setenv("HTTP_PROXY", f"http://{hung_peer}")
    monkeypatch.setenv("HTTPS_PROXY", f"http://{hung_peer}")
    monkeypatch.setenv("NO_PROXY", "")

    validator = SourceValidator(
        make_config(
            reddit_client_id="prismis-not-a-real-client-id",
            reddit_client_secret="prismis-not-a-real-client-secret",
        )
    )
    validator.timeout = 1.0

    is_valid, error, metadata = validator.validate_source(
        "https://reddit.com/r/python", "reddit"
    )

    assert is_valid is False
    assert metadata is None
    assert error is not None
    assert error.startswith("Network error contacting Reddit:"), (
        f"A raised probe must arrive as its own routed message, got: {error!r}"
    )
    assert not error.startswith("Validation failed:"), (
        "The blind except in validate_source swallowed a routed outcome — every cause "
        "would reach the operator as one string"
    )


def test_reddit_probe_is_cut_off_at_the_validators_budget(
    hung_peer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: A peer that accepts and never answers is cut off at
               SourceValidator.timeout, measured, not configured
    BREAKS: prawcore's own 16-second default applies instead, and the docstring's
            5-second promise is fiction on the path that most needs it
    NOTE: Asserting the values the builder just set proves the builder ran, not that
          anything is bounded. This waits on a real socket and reads the clock.
          What is bounded is each socket operation, not total elapsed time — requests
          turns a float timeout into a urllib3 Timeout with connect and read both set to
          it — so the upper bound below allows for more than one capped operation while
          staying far under the 16 seconds this transport exists to displace.
    """
    monkeypatch.setenv("HTTP_PROXY", f"http://{hung_peer}")
    monkeypatch.setenv("HTTPS_PROXY", f"http://{hung_peer}")
    monkeypatch.setenv("NO_PROXY", "")

    validator = SourceValidator(
        make_config(
            reddit_client_id="prismis-not-a-real-client-id",
            reddit_client_secret="prismis-not-a-real-client-secret",
        )
    )
    validator.timeout = 1.0

    started = time.monotonic()
    is_valid, error, _metadata = validator.validate_source("reddit://python", "reddit")
    elapsed = time.monotonic() - started

    assert is_valid is False
    assert error is not None
    assert elapsed >= validator.timeout * 0.8, (
        f"Returned in {elapsed:.2f}s — too fast to have waited on the socket at all, so "
        "this measured something other than the timeout"
    )
    assert elapsed < validator.timeout * 2, (
        f"Took {elapsed:.2f}s against a {validator.timeout}s budget. Either the clamp "
        "did not reach the request, leaving prawcore's 16-second default in force, or "
        "something is sleeping before the request the budget is supposed to cover"
    )
