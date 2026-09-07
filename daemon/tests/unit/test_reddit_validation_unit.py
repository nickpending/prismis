"""Unit tests for the Reddit validation path — parse, credential gate, interpret.

Every test here runs with no network and no credentials, so none is gated behind
PRISMIS_LIVE_NETWORK_TESTS or REDDIT_CLIENT_ID and all of them run in the gate.

Nothing is mocked, faked or stubbed. The probe is the only step that needs Reddit, and
it is the only step not exercised here: the two steps that hold decisions take plain
values, so real prawcore exceptions built over real requests responses drive them. The
one outcome reachable without a secret — a 401 from credentials Reddit refuses — is
proven end to end through the public entry point by the live test named
`test_reddit_invalid_credentials_are_named` in the validator integration suite.
"""

import time

import pytest
import requests
from prawcore import exceptions as prawcore_exceptions

from prismis_daemon.config import Config
from prismis_daemon.validator import (
    REDDIT_NOT_CONFIGURED,
    _DeadlineAdapter,
    _SingleAttemptRetry,
    SourceValidator,
)

from conftest import make_config

# A credential value with no substring in common with any message the validator emits,
# so an assertion that it is absent cannot pass by accident.
FAKE_CLIENT_ID = "zzclientidzz-8f3a1c"
FAKE_CLIENT_SECRET = "zzclientsecretzz-4b7e92"


def _reddit_config(**overrides: str) -> Config:
    """Build a Config carrying syntactically usable Reddit credentials."""
    fields = {
        "reddit_client_id": FAKE_CLIENT_ID,
        "reddit_client_secret": FAKE_CLIENT_SECRET,
    }
    fields.update(overrides)
    return make_config(**fields)


def _response(status: int, **headers: str) -> requests.Response:
    """Build the real response object prawcore hands to its own exceptions."""
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers)
    return response


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every outbound HTTP call at a proxy port nothing listens on.

    A test asserting the credential gate returns before the network can otherwise only
    infer it from the message. With this seal in place a request the gate failed to
    prevent never reaches Reddit — it dies as a proxy error, several seconds late and
    under a different message — so the assertions below are about the gate rather than
    about the wording. Verified by removing the gate and observing exactly that.
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("reddit://python", "python"),
        ("reddit://python/", "python"),
        ("https://reddit.com/r/LocalLLaMA", "LocalLLaMA"),
        ("https://www.reddit.com/r/rust/", "rust"),
        ("https://old.reddit.com/r/golang/comments/abc/title/", "golang"),
        ("https://reddit.com/r/python?sort=new", "python"),
        ("python", "python"),
        ("https://example.com/feed.xml", None),
        ("reddit://", None),
        ("", None),
    ],
)
def test_parse_covers_every_accepted_url_form(url: str, expected: str | None) -> None:
    """
    INVARIANT: Every URL form the daemon accepts still yields its subreddit name
    BREAKS: A source the operator could add yesterday is rejected as unparseable
    """
    assert SourceValidator()._parse_subreddit(url) == expected


@pytest.mark.parametrize(
    "config",
    [
        None,
        "empty-id",
        "empty-secret",
        "placeholder-id",
        "placeholder-secret",
    ],
)
def test_unusable_credentials_are_reported_without_a_request(
    config: str | None, no_network: None
) -> None:
    """
    INVARIANT: Absent credentials return "not configured" before any outbound request
    BREAKS: An install with no credentials is told its credentials are invalid, and the
            operator goes looking at Reddit for a problem that lives in their own config
    """
    configs = {
        None: None,
        "empty-id": _reddit_config(reddit_client_id=""),
        "empty-secret": _reddit_config(reddit_client_secret=""),
        "placeholder-id": _reddit_config(reddit_client_id="env:REDDIT_CLIENT_ID"),
        "placeholder-secret": _reddit_config(
            reddit_client_secret="env:REDDIT_CLIENT_SECRET"
        ),
    }
    validator = SourceValidator(configs[config])

    started = time.monotonic()
    is_valid, error, metadata = validator.validate_source("reddit://python", "reddit")
    elapsed = time.monotonic() - started

    assert is_valid is False
    assert error == REDDIT_NOT_CONFIGURED, (
        "Unconfigured credentials must be named as absent, not as invalid"
    )
    assert metadata is None
    assert elapsed < 1.0, (
        f"Returned in {elapsed:.2f}s — the gate must precede the network, and every "
        "outbound route is refused in this test"
    )


def test_usable_credentials_pass_the_gate() -> None:
    """
    INVARIANT: Credentials that are actually present get past the gate to the probe
    BREAKS: Reddit sources are unaddable on a correctly configured install — the gate
            becomes the outage it exists to explain
    """
    config, error = SourceValidator(_reddit_config())._reddit_credentials()

    assert error is None
    assert config is not None
    assert config.reddit_client_id == FAKE_CLIENT_ID


# Each row is one probe outcome and the substring that names its own cause. The
# messages are asserted pairwise distinct below: four different answers sharing one
# representation is what the constitution's second principle forbids, and it is the
# defect this whole mapping exists to remove.
OUTCOMES = [
    ("not-found", prawcore_exceptions.NotFound(_response(404)), "does not exist"),
    (
        "redirect",
        prawcore_exceptions.Redirect(
            _response(302, location="https://www.reddit.com/subreddits/search.json")
        ),
        "does not exist",
    ),
    (
        "forbidden",
        prawcore_exceptions.Forbidden(_response(403)),
        "private or quarantined",
    ),
    (
        "legal",
        prawcore_exceptions.UnavailableForLegalReasons(_response(451)),
        "unavailable for legal reasons",
    ),
    (
        "rate-limited",
        prawcore_exceptions.TooManyRequests(_response(429)),
        "rate limit exceeded",
    ),
    (
        "unauthorized",
        prawcore_exceptions.ResponseException(_response(401)),
        "credentials are invalid or expired",
    ),
    (
        "other-status",
        prawcore_exceptions.ResponseException(_response(503)),
        "HTTP 503",
    ),
    (
        "network",
        prawcore_exceptions.RequestException(
            requests.exceptions.ConnectionError("name resolution failed"),
            ("post", "https://www.reddit.com/api/v1/access_token"),
            {"data": FAKE_CLIENT_SECRET},
        ),
        "Network error contacting Reddit",
    ),
    (
        "unenumerated",
        prawcore_exceptions.InvalidInvocation("something prawcore added later"),
        "InvalidInvocation",
    ),
]


@pytest.mark.parametrize(
    ("label", "outcome", "expected_substring"),
    OUTCOMES,
    ids=[row[0] for row in OUTCOMES],
)
def test_each_probe_outcome_names_its_own_cause(
    label: str, outcome: Exception, expected_substring: str
) -> None:
    """
    INVARIANT: Every prawcore failure carries a message naming that failure
    BREAKS: A refused credential, an absent subreddit, a private one and a rate limit
            arrive as one string, and the operator cannot act on any of them
    """
    is_valid, error, metadata = SourceValidator()._interpret_reddit_outcome(
        "python", outcome
    )

    assert is_valid is False
    assert metadata is None
    assert error is not None
    assert expected_substring in error, f"{label}: {error!r}"
    assert not error.startswith("Validation failed:"), (
        f"{label} reached the blind except this repo suppresses the lint for"
    )
    assert not error.startswith("Reddit validation error:"), (
        f"{label} fell through to the unrouted catch-all"
    )


def test_probe_outcomes_are_distinguishable_by_cause() -> None:
    """
    INVARIANT: One message per cause, and no message shared between two causes
    BREAKS: Two different causes look identical to the caller, which is the collapse
            the constitution's second principle names
    NOTE: A 404 and a redirect to Reddit's search page answer the same question — there
          is no such subreddit — so they share a message by design and are one cause
          here. Every other row is its own.
    """
    validator = SourceValidator()

    by_cause: dict[str, set[str | None]] = {}
    for _label, outcome, expected in OUTCOMES:
        message = validator._interpret_reddit_outcome("python", outcome)[1]
        by_cause.setdefault(expected, set()).add(message)

    for cause, messages in by_cause.items():
        assert len(messages) == 1, f"{cause} produced more than one message: {messages}"

    emitted = [message for messages in by_cause.values() for message in messages]
    assert len(set(emitted)) == len(by_cause), (
        f"Two distinct causes share one message: {emitted}"
    )


@pytest.mark.parametrize(
    ("label", "outcome", "expected_substring"),
    OUTCOMES,
    ids=[row[0] for row in OUTCOMES],
)
def test_no_outcome_message_carries_a_credential(
    label: str, outcome: Exception, expected_substring: str
) -> None:
    """
    INVARIANT: No secret reaches a validation message
    BREAKS: prawcore's exception text flows into the API's 422 body, so a credential in
            a message is a credential in an HTTP response and in whatever logs it
    """
    _is_valid, error, _metadata = SourceValidator()._interpret_reddit_outcome(
        "python", outcome
    )

    assert error is not None
    assert FAKE_CLIENT_ID not in error, label
    assert FAKE_CLIENT_SECRET not in error, label


def test_success_carries_the_prefixed_display_name() -> None:
    """
    INVARIANT: A validated subreddit reports its prefixed display name as metadata
    BREAKS: Sources are named from the URL instead of r/Name, silently renaming every
            Reddit source added after this change
    """

    class _Fetched:
        """Stands for what the probe returns: an object carrying Reddit's own fields."""

        display_name_prefixed = "r/LocalLLaMA"

    is_valid, error, metadata = SourceValidator()._interpret_reddit_outcome(
        "LocalLLaMA", _Fetched()
    )

    assert is_valid is True
    assert error is None
    assert metadata == {"display_name": "r/LocalLLaMA"}


def test_probe_client_is_bounded_by_the_validators_timeout() -> None:
    """
    INVARIANT: The client the reddit path builds honours SourceValidator.timeout, makes
               no update check, and does not retry
    BREAKS: The documented 5-second budget is fiction — prawcore's own 16 seconds times
            three attempts applies instead, and construction reaches pypi.org first
    """
    validator = SourceValidator(_reddit_config())

    built_at = time.monotonic()
    reddit = validator._build_reddit_client(_reddit_config())

    assert reddit.config.check_for_updates is False, (
        "PRAW's constructor otherwise reaches pypi.org before any subreddit is touched"
    )
    assert reddit.read_only is True

    core = reddit._core
    assert core is not None
    assert core._retry_strategy_class is _SingleAttemptRetry
    assert _SingleAttemptRetry(3).should_retry_on_failure() is False, (
        "prawcore sleeps between attempts, and that sleep is outside the deadline"
    )

    adapter = core._requestor._http.get_adapter("https://oauth.reddit.com")
    assert isinstance(adapter, _DeadlineAdapter)
    budget = adapter._deadline - built_at
    assert budget == pytest.approx(validator.timeout, abs=0.5), (
        f"Probe budget is {budget:.2f}s, not the stated {validator.timeout}s"
    )


def test_deadline_adapter_refuses_a_request_past_its_budget() -> None:
    """
    INVARIANT: Once the budget is spent the transport stops rather than starting a call
    BREAKS: A probe that has already used its whole budget starts another request, and
            one validation outlives the timeout the class docstring promises
    """
    adapter = _DeadlineAdapter(-1.0)
    request = requests.Request("GET", "https://oauth.reddit.com/r/python").prepare()

    with pytest.raises(requests.exceptions.ConnectTimeout):
        adapter.send(request)
