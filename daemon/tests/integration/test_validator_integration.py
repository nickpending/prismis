"""Integration tests for SourceValidator against recorded third-party answers.

Everything a third party answers is replayed from a vcrpy cassette (`http_cassette`
fixture, conftest.py) under `no_network`, recorded once from the real service. Re-record
with PRISMIS_RECORD_HTTP=1 (see docs/architecture/boundaries.md); the Reddit cassettes
need Reddit credentials and are recorded on the host that holds them.

Failure modes a recording cannot reproduce are not replayed: a timeout is a loopback
listener that never answers, a 429 and a page that is not a feed are a loopback server
that answers them, and a host that does not resolve is the reserved `.invalid` TLD.
Each of those drives the real validator against something real this suite controls, and
none of them goes under `no_network` or a cassette, which would make the dead proxy or
the replay the cause of the failure being asserted.

A socket this suite opens on loopback and controls for the length of one test is not a
third party, and a bound proven against one is proven, where the same bound read back off
the object that just set it is not.

Refusing a credential and never having one are different answers, so the test that
proves the first needs no secret at all, while the tests that prove a subreddit's own
state were recorded with working ones.
"""

import os
import time

import pytest

from prismis_daemon.validator import SourceValidator

from conftest import LocalHttpServer, make_config

def reddit_validator() -> SourceValidator:
    """Build a validator carrying the credentials the environment supplies."""
    return SourceValidator(
        make_config(
            reddit_client_id=os.environ["REDDIT_CLIENT_ID"],
            reddit_client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        )
    )


@pytest.mark.usefixtures("http_cassette")
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


def test_invalid_sources_rejected() -> None:
    """
    INVARIANT: Invalid sources must be rejected with clear errors
    BREAKS: Bad sources pollute the database
    """
    validator = SourceValidator()

    # Test non-existent domain: `.invalid` is reserved (RFC 6761) and never resolves
    is_valid, error, _metadata = validator.validate_source(
        "https://no-such-host.invalid/feed.xml", "rss"
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


def test_network_timeout_handling(hung_peer: str) -> None:
    """
    FAILURE MODE: Network timeouts must fail gracefully
    GRACEFUL: Clear error message, no hanging
    """
    validator = SourceValidator()

    # A short budget against a peer that accepts the connection and never answers, so the
    # read is what times out
    validator.timeout = 0.5

    is_valid, error, _metadata = validator.validate_source(
        f"http://{hung_peer}/feed.xml", "rss"
    )
    assert is_valid is False, "Timeout should fail validation"
    assert error is not None, "Should have error message"
    assert "timed out" in error.lower(), "Should mention timeout"


def test_malformed_rss_handling(local_http_server: LocalHttpServer) -> None:
    """
    FAILURE MODE: Malformed RSS/XML must be rejected
    GRACEFUL: Clear error about invalid feed format
    """
    validator = SourceValidator()

    # A page that answers 200 with HTML instead of a feed
    local_http_server.routes["/"] = (
        200,
        "text/html",
        b"<!doctype html><html><head><title>Home</title></head>"
        b"<body><p>Not a feed.</p></body></html>",
    )
    is_valid, error, _metadata = validator.validate_source(
        f"{local_http_server.base_url}/",
        "rss",
    )
    assert is_valid is False, "HTML page should fail RSS validation"
    assert error is not None, "Should have error message"
    assert "invalid" in error.lower() or "format" in error.lower(), (
        "Should mention invalid format"
    )


def test_rate_limited_feed_is_reported_with_its_status(
    local_http_server: LocalHttpServer,
) -> None:
    """
    FAILURE MODE: A feed host answering 429 must fail validation, naming the status
    GRACEFUL: The operator sees HTTP 429, not a parse error from an empty body
    """
    local_http_server.routes["/feed.xml"] = (429, "text/plain", b"slow down")

    is_valid, error, _metadata = SourceValidator().validate_source(
        f"{local_http_server.base_url}/feed.xml", "rss"
    )

    assert local_http_server.requests == ["/feed.xml"], "the validator must ask the server"
    assert is_valid is False
    assert error is not None
    assert error.startswith("HTTP 429"), f"Should name the status, got: {error!r}"


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
        "https://no-such-host.invalid/status/429"
    )

    assert is_valid is False
    assert error == "Could not extract subreddit name from URL"
    assert metadata is None


@pytest.mark.usefixtures("http_cassette")
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


@pytest.mark.usefixtures("reddit_credentials", "http_cassette")
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


@pytest.mark.usefixtures("reddit_credentials", "http_cassette")
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


@pytest.mark.usefixtures("reddit_credentials", "http_cassette")
def test_reddit_private_subreddit_handling() -> None:
    """
    FAILURE MODE: A subreddit that exists but refuses access returns 403
    GRACEFUL: The message says private or quarantined, not missing
    NOTE: r/lounge is restricted to Reddit Premium members, and the cassette holds the
          403 Reddit answered with. A change on Reddit's side shows only on re-recording.
    """
    is_valid, error, _metadata = reddit_validator().validate_source(
        "https://reddit.com/r/lounge", "reddit"
    )

    assert is_valid is False, "r/lounge was recorded as restricted"
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
