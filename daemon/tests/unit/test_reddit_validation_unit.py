"""Unit tests for the Reddit validation path — parse, credential gate, interpret.

Every test here runs with no network and no credentials, so none is gated behind
PRISMIS_LIVE_NETWORK_TESTS or REDDIT_CLIENT_ID and all of them run in the gate.

Nothing is mocked, faked or stubbed. The probe is the only step that needs Reddit, and
it is the only step not exercised here: the two steps that hold decisions take plain
values, so real prawcore exceptions built over real requests responses drive them.

Driving a helper directly proves the helper, never the composition around it, so the
composition is proven elsewhere and every one of those is a real failure rather than a
described one. `test_validate_source_routes_a_real_probe_failure` in the validator
integration suite drives a raised probe out through the public method against a socket
it owns, and runs in the gate; `test_reddit_invalid_credentials_are_named`, in the same
file behind the live-network gate, does it for a 401 Reddit itself returns.
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

    This is containment, not the assertion. It guarantees that a gate failure cannot
    reach Reddit from a gate-run test; what proves the gate fired is the message, and it
    proves it for every row, because no path that reaches the network can produce the
    not-configured message. Removing the gate produces a different message either way —
    a proxy error for the rows carrying credentials, and an attribute error on the None
    config, which is why there is no timing assertion here: one row fails fast and the
    others fail slow, so elapsed time cannot discriminate across the set.
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

    is_valid, error, metadata = validator.validate_source("reddit://python", "reddit")

    assert is_valid is False
    assert error == REDDIT_NOT_CONFIGURED, (
        "Unconfigured credentials must be named as absent, not as invalid — and no "
        "path that reaches the network can produce this message"
    )
    assert metadata is None


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
    # Reddit refuses a bad client id or secret with HTTP 200 and an error in the token
    # payload, which prawcore raises as this — a direct PrawcoreException subclass, so
    # the status check that catches the row above cannot see it.
    (
        "oauth-refused",
        prawcore_exceptions.OAuthException(
            _response(200), "invalid_grant", "credentials were rejected"
        ),
        "credentials are invalid or expired",
    ),
    # prawcore's authorization-error mapping holds 403 and two OAuth error strings and
    # no 401, so a 401 with no www-authenticate header raises this bare rather than any
    # exception of its own. Same for a token payload missing the fields it reads.
    ("auth-mapping-miss", KeyError(401), "credentials are invalid or expired"),
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
    assert not error.startswith("Reddit validation error:"), (
        f"{label} fell through to the unrouted tail"
    )
    # The blind except in validate_source is the other collapse, and it cannot be
    # reached from here — that string is only emitted one level up. The test named
    # test_validate_source_routes_a_real_probe_failure, in the validator integration
    # suite, is what holds it, because it drives the public method.


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


class _HostileURL(str):
    """A URL whose own failure carries the credential in its text.

    The blind except in `validate_source` renders whatever `str()` an escaping exception
    produces. Nothing in the tree routes a credential there today, which is exactly why
    it needs a test: the redaction has to hold for text nobody has predicted, and the
    only way to assert that is to send text nobody predicted through it.
    """

    def startswith(
        self, prefix: object, start: object = None, end: object = None
    ) -> bool:
        raise RuntimeError(f"upstream failure quoting {FAKE_CLIENT_SECRET}")


def test_unrouted_outcome_text_is_redacted() -> None:
    """
    INVARIANT: The unrouted tail strips credentials out of the exception text it renders
    BREAKS: An exception carrying the client secret becomes a 422 body carrying it —
            `add_source` wraps this message verbatim, so the response is the exit route
    """
    validator = SourceValidator(_reddit_config())

    _is_valid, error, _metadata = validator._interpret_reddit_outcome(
        "python", RuntimeError(f"unmapped failure quoting {FAKE_CLIENT_SECRET}")
    )

    assert error is not None
    assert error.startswith("Reddit validation error:"), (
        f"This case must reach the unrouted tail to test it: {error!r}"
    )
    assert FAKE_CLIENT_SECRET not in error, error
    assert "[redacted]" in error


def test_network_error_text_is_redacted() -> None:
    """
    INVARIANT: The network-failure message strips credentials out of the wrapped cause
    BREAKS: prawcore hands the whole request — credentials included — to
            RequestException, and this message renders its original exception
    """
    validator = SourceValidator(_reddit_config())
    outcome = prawcore_exceptions.RequestException(
        requests.exceptions.ConnectionError(f"refused while sending {FAKE_CLIENT_ID}"),
        ("post", "https://www.reddit.com/api/v1/access_token"),
        {"data": FAKE_CLIENT_SECRET},
    )

    _is_valid, error, _metadata = validator._interpret_reddit_outcome("python", outcome)

    assert error is not None
    assert error.startswith("Network error contacting Reddit:")
    assert FAKE_CLIENT_ID not in error, error
    assert "[redacted]" in error


def test_the_blind_except_redacts_what_escapes_into_it() -> None:
    """
    INVARIANT: The catch-all in validate_source strips credentials out of the exception
               text it renders
    BREAKS: The one message built from text no branch predicted is the one message that
            can carry anything, including a secret, straight into a 422 body
    """
    validator = SourceValidator(_reddit_config())

    is_valid, error, _metadata = validator.validate_source(
        _HostileURL("reddit://python"), "reddit"
    )

    assert is_valid is False
    assert error is not None
    assert error.startswith("Validation failed:"), (
        f"This case must reach the catch-all to test it: {error!r}"
    )
    assert FAKE_CLIENT_SECRET not in error, error
    assert "[redacted]" in error


def test_redaction_leaves_a_message_alone_when_there_is_no_credential() -> None:
    """
    INVARIANT: Redaction removes credential values and nothing else
    BREAKS: Over-broad redaction eats the diagnostic text, and every failure reads the
            same to the operator — the collapse this whole path exists to prevent
    """
    validator = SourceValidator(_reddit_config())

    assert validator._redact("HTTP 503 from Reddit") == "HTTP 503 from Reddit"
    assert SourceValidator()._redact(f"carrying {FAKE_CLIENT_SECRET}") == (
        f"carrying {FAKE_CLIENT_SECRET}"
    ), "With no config there is no configured value to recognise — say so, don't guess"


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
