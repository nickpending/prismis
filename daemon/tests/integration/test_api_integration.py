"""Integration tests for REST API - protecting invariants and handling failures."""

import asyncio
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon import api
from prismis_daemon.api import app, get_validator
from prismis_daemon.auth import CONFIG_UNAVAILABLE_MESSAGE
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from prismis_daemon.validator import REDDIT_NOT_CONFIGURED
from conftest import TEST_API_KEY


@pytest.fixture
def api_client(test_db) -> TestClient:
    """Create test client for API, against an initialized test database."""
    return TestClient(app)


def test_api_auth_required(api_client: TestClient) -> None:
    """
    INVARIANT: API key required for all protected endpoints
    BREAKS: Unauthorized access to source management
    """
    # Test all protected endpoints without API key
    protected_endpoints = [
        ("GET", "/api/sources"),
        ("POST", "/api/sources", {"url": "https://example.com", "type": "rss"}),
        ("DELETE", "/api/sources/test-id"),
    ]

    for method, path, *data in protected_endpoints:
        if method == "GET":
            response = api_client.get(path)
        elif method == "POST":
            response = api_client.post(path, json=data[0])
        elif method == "DELETE":
            response = api_client.delete(path)

        assert response.status_code == 403, f"{method} {path} must require auth"
        data = response.json()
        assert data["success"] is False
        assert "API key" in data["message"]

    # Test with invalid API key
    response = api_client.get("/api/sources", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 403

    # Test with valid API key
    response = api_client.get("/api/sources", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200


@pytest.mark.skipif(
    not os.environ.get("PRISMIS_LIVE_NETWORK_TESTS"),
    reason="Adds sources through POST /api/sources, which validates the rss row against "
    "a live third-party feed. Set PRISMIS_LIVE_NETWORK_TESTS=1 to run.",
)
def test_url_normalization(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: Special protocol URLs must be normalized to real URLs
    BREAKS: Fetchers expect real URLs, not protocol URLs
    NOTE: The reddit rows moved to the companion test below. Reddit validation now needs
          credentials, so on a host without them the credential gate refuses the source
          and the add returns a 422 — which would read here as a normalization failure.
    """
    test_cases = [
        # (input_url, type, expected_normalized)  # noqa: ERA001 - prose, not code
        (
            "youtube://UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "youtube",
            "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
        ),
        ("youtube://@fireship", "youtube", "https://www.youtube.com/@fireship"),
        (
            "https://simonwillison.net/atom/everything/",
            "rss",
            "https://simonwillison.net/atom/everything/",
        ),  # Real RSS feed
    ]

    for input_url, source_type, expected_url in test_cases:
        # Add source via API
        response = api_client.post(
            "/api/sources",
            json={"url": input_url, "type": source_type},
            headers={"X-API-Key": TEST_API_KEY},
        )

        # API should return normalized URL
        assert response.status_code == 200, f"Failed for {input_url}: {response.json()}"
        data = response.json()
        assert data["success"] is True
        assert data["data"]["url"] == expected_url, f"Failed to normalize {input_url}"

        # Verify database stores normalized URL
        storage = Storage(test_db)
        sources = storage.get_all_sources()
        source = next((s for s in sources if s["id"] == data["data"]["id"]), None)
        assert source is not None
        assert source["url"] == expected_url, "Database should store normalized URL"


@pytest.mark.skipif(
    not os.environ.get("PRISMIS_LIVE_NETWORK_TESTS")
    or not os.environ.get("REDDIT_CLIENT_ID")
    or not os.environ.get("REDDIT_CLIENT_SECRET"),
    reason="Adds reddit sources through POST /api/sources, which now probes Reddit's "
    "authenticated API. Set PRISMIS_LIVE_NETWORK_TESTS=1 and BOTH REDDIT_CLIENT_ID and "
    "REDDIT_CLIENT_SECRET to run.",
)
def test_url_normalization_reddit(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: reddit:// URLs are normalized to real URLs on the way into the database
    BREAKS: The fetcher gets a protocol URL it cannot resolve
    NOTE: Split from the test above because these rows need credentials as well as
          network. The stored name comes from the subreddit's own prefixed display name,
          which is why the URL rather than the name is what is asserted here.
    """
    test_cases = [
        # (input_url, expected_normalized)  # noqa: ERA001 - prose, not code
        ("reddit://rust", "https://www.reddit.com/r/rust"),
        ("reddit://python", "https://www.reddit.com/r/python"),
    ]

    for input_url, expected_url in test_cases:
        response = api_client.post(
            "/api/sources",
            json={"url": input_url, "type": "reddit"},
            headers={"X-API-Key": TEST_API_KEY},
        )

        assert response.status_code == 200, f"Failed for {input_url}: {response.json()}"
        data = response.json()
        assert data["success"] is True
        assert data["data"]["url"] == expected_url, f"Failed to normalize {input_url}"

        storage = Storage(test_db)
        sources = storage.get_all_sources()
        source = next((s for s in sources if s["id"] == data["data"]["id"]), None)
        assert source is not None
        assert source["url"] == expected_url, "Database should store normalized URL"


def test_source_type_validation_blocks_invalid(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: Sources with invalid type-URL combinations never enter database.
    BREAKS: Fetchers crash on sources that don't match their declared type.

    These cases are rejected by source-type validators in api.py (the
    _validate_source_url() logic at api.py:217+), NOT by
    SourceRequest.validate_url. If SourceRequest.validate_url were deleted
    entirely, all five cases below would still return 422 for type-mismatch
    reasons.

    The empty/whitespace URL behavioral invariant (SourceRequest.validate_url's
    strip + non-empty check) is covered in:
        tests/unit/test_source_request_validator_unit.py
    """
    invalid_sources = [
        # These should all be rejected
        ("not-a-url", "rss"),
        ("https://definitely-not-a-real-domain-12345.com/feed.xml", "rss"),
        ("reddit://", "reddit"),  # Empty subreddit
        ("youtube://", "youtube"),  # Empty channel
        (
            "https://simonwillison.net/atom/everything/",
            "reddit",
        ),  # RSS URL for reddit type
    ]

    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    for url, source_type in invalid_sources:
        response = api_client.post(
            "/api/sources",
            json={"url": url, "type": source_type},
            headers={"X-API-Key": TEST_API_KEY},
        )

        # Should reject with 422 validation error
        assert response.status_code == 422, f"Should reject invalid source: {url}"
        data = response.json()
        assert data["success"] is False
        assert "validation failed" in data["message"].lower()

    # Verify no invalid sources were added
    final_count = len(storage.get_all_sources())
    assert final_count == initial_count, "No invalid sources should be added"


def test_cascade_delete(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: Deleting source removes ALL its content
    BREAKS: Orphaned content in database
    """
    storage = Storage(test_db)

    # Add a real source
    response = api_client.post(
        "/api/sources",
        json={"url": "https://simonwillison.net/atom/everything/", "type": "rss"},
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200
    source_id = response.json()["data"]["id"]

    # Add content for this source
    content_items = [
        ContentItem(
            external_id=f"item-{i}",
            source_id=source_id,
            title=f"Test Item {i}",
            url=f"https://example.com/item-{i}",
            content=f"Content {i}",
            published_at=None,
        )
        for i in range(5)
    ]

    for item in content_items:
        storage.add_content(item)

    # Verify content exists by checking each priority level
    # Since we didn't set priority, they should be None/low priority
    conn = storage.conn
    cursor = conn.execute(
        "SELECT COUNT(*) FROM content WHERE source_id = ?", (source_id,)
    )
    content_count = cursor.fetchone()[0]
    assert content_count == 5

    # Delete the source via API
    response = api_client.delete(
        f"/api/sources/{source_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200

    # Verify source is gone
    sources = storage.get_all_sources()
    assert not any(s["id"] == source_id for s in sources)

    # Verify ALL content is gone (cascade delete)
    conn = storage.conn
    cursor = conn.execute(
        "SELECT COUNT(*) FROM content WHERE source_id = ?", (source_id,)
    )
    content_count = cursor.fetchone()[0]
    assert content_count == 0, "All content should be cascade deleted"


### CHECKPOINT 7: Implement Failure Mode Tests


def test_concurrent_source_adds(api_client: TestClient, test_db: Path) -> None:
    """
    FAILURE: Database locks during concurrent writes
    GRACEFUL: All requests eventually succeed with retries
    """
    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    # Use different real RSS feeds for variety
    feeds = [
        "https://simonwillison.net/atom/everything/",
        "https://xkcd.com/rss.xml",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://hnrss.org/frontpage",
        "https://www.reddit.com/r/programming/.rss",
    ]

    # TestClient doesn't support true concurrency, but we can test rapid sequential adds
    # which will still test database locking and retry logic
    successful_adds = 0
    for i in range(10):
        feed_url = feeds[i % len(feeds)]
        response = api_client.post(
            "/api/sources",
            json={
                "url": feed_url,
                "type": "rss",
                "name": f"Feed {i}",
            },
            headers={"X-API-Key": TEST_API_KEY},
        )
        if response.status_code == 200:
            successful_adds += 1

    # Should successfully add at least the 5 unique feeds
    assert successful_adds >= 5, f"Only {successful_adds} adds succeeded"

    # Verify at least 5 unique sources were added
    final_count = len(storage.get_all_sources())
    assert final_count >= initial_count + 5, "At least 5 unique sources should be added"


def test_validation_timeout(api_client: TestClient) -> None:
    """
    FAILURE: Source validation exceeds 5 second timeout
    GRACEFUL: Returns error quickly, doesn't hang
    """
    # Use a URL that will timeout (non-routable IP)
    start_time = time.time()

    response = api_client.post(
        "/api/sources",
        json={
            "url": "http://192.0.2.1/feed.xml",  # Non-routable IP (TEST-NET-1)
            "type": "rss",
        },
        headers={"X-API-Key": TEST_API_KEY},
    )

    elapsed = time.time() - start_time

    # Should fail with validation error
    assert response.status_code == 422

    # Should timeout within ~5 seconds (not hang forever)
    assert elapsed < 10, f"Validation took {elapsed}s, should timeout at 5s"
    data = response.json()
    assert data["success"] is False
    assert "validation failed" in data["message"].lower()


### CHECKPOINT 8: Implement Confidence Tests


def test_api_performance(api_client: TestClient, test_db: Path) -> None:
    """
    CONFIDENCE: API responses within reasonable time for CRUD operations
    THRESHOLD: Adjusted for real network validation
    """
    # Add some test data first using direct storage (to avoid network delays)
    storage = Storage(test_db)
    for i in range(20):
        storage.add_source(f"https://example{i}.com/feed.xml", "rss", f"Feed {i}")

    operations = []

    # Test GET performance (20 sources)
    start = time.time()
    response = api_client.get("/api/sources", headers={"X-API-Key": TEST_API_KEY})
    elapsed = time.time() - start
    assert response.status_code == 200
    operations.append(("GET", elapsed))

    # Test POST performance with real URL
    start = time.time()
    response = api_client.post(
        "/api/sources",
        json={"url": "https://simonwillison.net/atom/everything/", "type": "rss"},
        headers={"X-API-Key": TEST_API_KEY},
    )
    elapsed = time.time() - start
    assert response.status_code == 200
    source_id = response.json()["data"]["id"]
    operations.append(("POST", elapsed))

    # Test DELETE performance
    start = time.time()
    response = api_client.delete(
        f"/api/sources/{source_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    elapsed = time.time() - start
    assert response.status_code == 200
    operations.append(("DELETE", elapsed))

    # GET and DELETE should be fast, POST can take longer due to validation
    assert operations[0][1] < 0.5, (
        f"GET took {operations[0][1] * 1000:.0f}ms, should be <500ms"
    )
    assert operations[2][1] < 0.5, (
        f"DELETE took {operations[2][1] * 1000:.0f}ms, should be <500ms"
    )
    # POST with real validation might take up to 10 seconds
    assert operations[1][1] < 10, f"POST took {operations[1][1]:.1f}s, should be <10s"


def test_add_file_source_end_to_end(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: POST /api/sources creates a source that reads back from the database
    BREAKS: The endpoint's own wiring — normalization, validation, storage, response —
            is unproven, and a break in it is only found by a user adding a source

    NOTE: A file source is what makes this deterministic. Its validation is an extension
          check and a scheme check, so the test asserts the endpoint rather than a third
          party's uptime, and it does so without standing anything in for the validator,
          the storage layer or the client.
    """
    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    response = api_client.post(
        "/api/sources",
        json={
            "url": "https://example.com/notes/reading-list.md",
            "type": "file",
            "name": "Reading list",
        },
        headers={"X-API-Key": TEST_API_KEY},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["type"] == "file"
    assert body["data"]["url"] == "https://example.com/notes/reading-list.md"
    assert body["data"]["name"] == "Reading list"
    source_id = body["data"]["id"]

    stored = storage.get_all_sources()
    assert len(stored) == initial_count + 1, "The source must reach the database"
    created = next(s for s in stored if s["id"] == source_id)
    assert created["url"] == "https://example.com/notes/reading-list.md"
    assert created["type"] == "file"
    assert created["name"] == "Reading list"

    listed = api_client.get("/api/sources", headers={"X-API-Key": TEST_API_KEY}).json()
    assert any(s["id"] == source_id for s in listed["data"]["sources"]), (
        "A created source must be readable back through the API"
    )


def test_add_file_source_rejects_an_unsupported_extension(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: The endpoint refuses a file URL the fetcher cannot read
    BREAKS: An unreadable source enters the database and fails every fetch cycle after

    NOTE: This is the other half of the pair above — a deterministic rejection through
          the same path, which is what proves the success case was decided rather than
          merely defaulted to.
    """
    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    response = api_client.post(
        "/api/sources",
        json={"url": "https://example.com/notes/reading-list.pdf", "type": "file"},
        headers={"X-API-Key": TEST_API_KEY},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert "validation failed" in body["message"].lower()
    assert len(storage.get_all_sources()) == initial_count, "Nothing may be stored"


def test_validator_dependency_degrades_when_config_will_not_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: A config that will not load leaves non-reddit validation working and the
               reddit path reporting its own distinguishable "not configured"
    BREAKS: One unloadable config takes down validation for source types that never
            needed a config, and reddit reports credentials as invalid on a machine that
            has none

    NOTE: The provider is where this behaviour lives, so the provider is what this
          exercises. It is not reachable through an endpoint on such an install and is
          not meant to be: the `verify_api_key` guard in
          `daemon/src/prismis_daemon/auth.py` loads the same config to resolve the
          expected API key and is a route-level Security dependency, so the request is
          refused before any handler dependency resolves. The companion test below holds
          that refusal in place deliberately.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))

    validator = asyncio.run(get_validator())

    assert validator.config is None, "An unloadable config must degrade, not propagate"

    is_valid, error, _metadata = validator.validate_source(
        "https://example.com/notes.md", "file"
    )
    assert is_valid is True, f"File validation needs no config: {error}"

    is_valid, error, _metadata = validator.validate_source(
        "youtube://@mkbhd", "youtube"
    )
    assert is_valid is True, f"YouTube validation needs no config: {error}"

    is_valid, error, _metadata = validator.validate_source("reddit://python", "reddit")
    assert is_valid is False
    assert error == REDDIT_NOT_CONFIGURED, (
        f"Reddit must name the absent config, not blame the credentials: {error!r}"
    )


def test_unloadable_config_fails_closed_inside_the_envelope(
    test_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: An install whose config will not load refuses every authenticated
               request, and refuses it inside the {success, message, data} envelope
    BREAKS: Either half is a defect. Serving the request means an install that cannot
            know its expected API key accepted one anyway; losing the envelope means the
            TUI and CLI get a body they cannot parse and show nothing actionable

    NOTE: The refusal is the correct posture and must not be "fixed". The expected API
          key comes from the same config, so an install that cannot load it cannot know
          what to compare against — the `verify_api_key` guard in
          `daemon/src/prismis_daemon/auth.py` is a route-level Security dependency and
          refuses first, whatever the source type. Failing closed is the answer; making
          these adds succeed would mean changing where the key comes from.

          The message is asserted whole rather than by keyword. A substring check passes
          for any failure that happens to mention configuration, including one that
          reached the body by rendering the underlying exception — which is what used to
          happen, and what put the absolute config path in front of a caller holding no
          valid key.
    """
    config_home = tmp_path / "empty"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    client = TestClient(app)

    for payload in (
        {"url": "https://example.com/notes.md", "type": "file"},
        {"url": "youtube://@mkbhd", "type": "youtube"},
    ):
        response = client.post(
            "/api/sources", json=payload, headers={"X-API-Key": TEST_API_KEY}
        )

        assert response.status_code >= 400, (
            f"{payload['type']}: an install that cannot resolve its own API key must "
            f"refuse the request, not serve it (got {response.status_code})"
        )
        assert response.headers["content-type"].startswith("application/json"), (
            f"{payload['type']}: response must stay JSON"
        )
        body = response.json()
        assert set(body) == {"success", "message", "data"}, (
            f"{payload['type']}: envelope must survive an unloadable config: {body}"
        )
        assert body["success"] is False
        assert body["message"] == CONFIG_UNAVAILABLE_MESSAGE, (
            f"{payload['type']}: the refusal must say only that the config would not "
            f"load, got: {body['message']!r}"
        )
        assert str(config_home) not in body["message"], (
            "The config path is internal detail and the caller holds no valid key"
        )


def test_slow_validation_is_refused_rather_than_held_open(
    api_client: TestClient,
    test_db: Path,
    hung_peer: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    INVARIANT: A validation that outruns the server budget comes back as a 422 naming
               the timeout, not as a request held open until something else gives up
    BREAKS: A source whose host accepts and never answers holds an API request for as
            long as the validator will wait, and the operator gets whatever their own
            client does on timeout — which names nothing

    NOTE: The budget is lowered for the test, not the slowness faked: the peer is a real
          listener this test owns that accepts and never answers, so the validator does
          block. Waiting out the real budget would put twenty seconds in the gate to
          prove a branch that does not depend on the number.

          What proves the outer bound fired is the message. The validator's own timeout
          answers with "Request timed out after 5 seconds" and only this branch says
          "Source validation timed out", so the two cannot be confused. Elapsed time
          cannot serve here: the outer bound stops the waiting but cannot kill the
          worker thread, and TestClient does not return until that thread drains, so
          this test takes about five seconds while the daemon's own request log records
          the answer in tens of milliseconds.
    """
    monkeypatch.setattr(api, "SOURCE_VALIDATION_TIMEOUT", 0.05)
    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    response = api_client.post(
        "/api/sources",
        json={"url": f"http://{hung_peer}/feed.xml", "type": "rss"},
        headers={"X-API-Key": TEST_API_KEY},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert "Source validation timed out" in body["message"], (
        "Only the outer bound emits this. Anything else means the validator's own "
        f"timeout answered and the branch under test never ran: {body['message']!r}"
    )
    assert len(storage.get_all_sources()) == initial_count, "Nothing may be stored"


def test_server_validation_budget_stays_under_every_client_budget() -> None:
    """
    INVARIANT: The server's validation budget leaves headroom under every client's
    BREAKS: The client's clock expires first, so the daemon's message — the only one
            that names which source overran — can never reach an operator. Tying the two
            to the same number is what makes the timeout branch above dead in production
            while still passing its own test

    NOTE: This reads the client sources rather than restating their values, so an edit
          on either side lands here instead of going unnoticed. If a pattern below stops
          matching, the budget it guarded is unknown, and unknown fails.
    """
    root = Path(__file__).parents[3]
    cli_source = (root / "cli/src/cli/api_client.py").read_text()
    tui_source = (root / "tui/internal/api/client.go").read_text()

    cli_match = re.search(r"httpx\.Timeout\(\s*([\d.]+)", cli_source)
    tui_match = re.search(
        r"ResponseHeaderTimeout:\s*(\d+)\s*\*\s*time\.Second", tui_source
    )

    assert cli_match, "Could not find the CLI's httpx.Timeout — re-derive the budget"
    assert tui_match, (
        "Could not find the TUI's ResponseHeaderTimeout — re-derive the budget"
    )

    smallest_client_budget = min(float(cli_match.group(1)), float(tui_match.group(1)))

    assert api.SOURCE_VALIDATION_TIMEOUT + 5.0 <= smallest_client_budget, (
        f"Server budget {api.SOURCE_VALIDATION_TIMEOUT}s leaves less than 5s under the "
        f"smallest client budget {smallest_client_budget}s. The daemon needs time to "
        "build and send its answer after the budget expires, or the client times out "
        "first and the answer is never seen"
    )
