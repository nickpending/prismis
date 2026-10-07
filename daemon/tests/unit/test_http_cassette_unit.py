"""The record/replay mechanism behind `http_cassette`, and what no cassette may hold.

Cassettes here are written by the same vcrpy configuration the suite records with
(`make_vcr`), from a real loopback server this test owns, into a per-test temp directory;
what a replay does is decided by the file the test wrote, not by the committed ones. The
loopback server counts what it was asked, so "never reached the network" is measured by a
request count that has to stay put, not by an absent error.

Every committed cassette is also read here, so a credential that slipped past the scrub
in a recording made on another machine fails this suite, not a review.
"""

import threading
import http.server
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import requests
import yaml

from conftest import (
    ACCESS_TOKEN_PLACEHOLDER,
    HTTP_CASSETTES_DIR,
    HttpCassette,
    LocalHttpServer,
    make_vcr,
)
from prismis_daemon.article_extractor import extract_article

ARTICLE = (
    b"<!doctype html><html><head><title>Recorded post</title></head><body><article>"
    + b"<h1>Recorded post</h1>"
    + b"".join(
        b"<p>Paragraph %d of the recorded article explains how the cassette stands in "
        b"for the site, with enough prose in it for the extractor to keep.</p>" % n
        for n in range(1, 9)
    )
    + b"</article></body></html>"
)
REAL_TOKEN = "REAL-TOKEN-VALUE-1234567890"


@pytest.fixture
def http_cassettes_dir(tmp_path: Path) -> Path:
    return tmp_path / "cassettes"


@pytest.fixture
def seeded_cassette(
    request: pytest.FixtureRequest,
    http_cassettes_dir: Path,
    local_http_server: LocalHttpServer,
) -> str:
    """Record `/recorded` from the loopback server into the test's own cassette.

    Runs before `http_cassette` opens that cassette (list it first in the test's
    arguments), and returns the recorded URL.
    """
    local_http_server.routes["/recorded"] = (200, "text/html", ARTICLE)
    path = http_cassettes_dir / request.module.__name__ / f"{request.node.name}.yaml"
    url = f"{local_http_server.base_url}/recorded"
    with make_vcr(True).use_cassette(str(path)):
        assert httpx.get(url).content == ARTICLE
    assert local_http_server.requests == ["/recorded"]
    return url


def test_a_recorded_request_replays_without_reaching_the_server(
    seeded_cassette: str,
    http_cassette: HttpCassette,
    local_http_server: LocalHttpServer,
) -> None:
    assert httpx.get(seeded_cassette).content == ARTICLE

    assert local_http_server.requests == ["/recorded"], "replay must not ask the server"
    assert http_cassette.failures == []


def test_a_request_the_cassette_lacks_is_refused_naming_the_record_command(
    seeded_cassette: str,
    http_cassette: HttpCassette,
    local_http_server: LocalHttpServer,
) -> None:
    local_http_server.routes["/unrecorded"] = (200, "text/plain", b"live answer")

    with pytest.raises(Exception, match="Can't overwrite existing cassette") as raised:
        httpx.get(f"{local_http_server.base_url}/unrecorded")

    assert "PRISMIS_RECORD_HTTP=1" in str(raised.value)
    assert str(http_cassette.path) in str(raised.value)
    assert local_http_server.requests == ["/recorded"], (
        "the refused request must never have reached the server"
    )
    assert len(http_cassette.failures) == 1

    # The recorded request is still unused, so the cassette is not used up either way;
    # replay it, then show the refusal alone fails the test at teardown.
    assert httpx.get(seeded_cassette).content == ARTICLE
    with pytest.raises(pytest.fail.Exception) as failed:
        http_cassette.finalize()
    assert "PRISMIS_RECORD_HTTP=1" in str(failed.value)
    http_cassette.failures.clear()


def test_a_refusal_the_code_under_test_swallowed_still_fails_the_test(
    seeded_cassette: str,
    http_cassette: HttpCassette,
    local_http_server: LocalHttpServer,
) -> None:
    assert httpx.get(seeded_cassette).content == ARTICLE
    try:
        httpx.get(f"{local_http_server.base_url}/unrecorded")
    except Exception:  # noqa: S110 - the code under test swallows, which is the point
        pass

    with pytest.raises(pytest.fail.Exception) as failed:
        http_cassette.finalize()

    assert str(http_cassette.path) in str(failed.value)
    http_cassette.failures.clear()


def test_a_cassette_request_the_test_never_made_fails_the_test(
    seeded_cassette: str, http_cassette: HttpCassette
) -> None:
    with pytest.raises(pytest.fail.Exception) as failed:
        http_cassette.finalize()

    assert "did not make every request recorded" in str(failed.value)
    assert "PRISMIS_RECORD_HTTP=1" in str(failed.value)
    assert httpx.get(seeded_cassette).content == ARTICLE


def test_a_missing_cassette_refuses_the_request_and_names_the_file(
    http_cassette: HttpCassette, local_http_server: LocalHttpServer
) -> None:
    local_http_server.routes["/x"] = (200, "text/plain", b"live")

    with pytest.raises(Exception, match="Can't overwrite existing cassette"):
        httpx.get(f"{local_http_server.base_url}/x")

    assert local_http_server.requests == []
    assert str(http_cassette.path) in http_cassette.failures[0]
    assert "PRISMIS_RECORD_HTTP=1" in http_cassette.failures[0]
    http_cassette.failures.clear()
    assert not http_cassette.path.exists()
    with pytest.raises(pytest.fail.Exception, match="No HTTP cassette exists"):
        http_cassette.finalize()
    http_cassette.recording = True  # nothing was recorded; keep the fixture's teardown quiet


def test_article_extraction_is_served_from_the_cassette_under_no_network(
    seeded_cassette: str,
    http_cassette: HttpCassette,
    local_http_server: LocalHttpServer,
) -> None:
    import os

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:1", "must run under no_network"

    result = extract_article(seeded_cassette)

    assert result.outcome == "extracted"
    assert result.text is not None and "Paragraph 3 of the recorded article" in result.text
    assert local_http_server.requests == ["/recorded"], (
        "extract_article's urllib opener went around the cassette to the server"
    )


class _TokenServer:
    """A loopback server answering like Reddit's token endpoint, cookie and all."""

    def __init__(self) -> None:
        self.seen_authorization: list[str | None] = []
        owner = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                owner.seen_authorization.append(self.headers.get("Authorization"))
                body = (
                    '{"access_token": "%s", "token_type": "bearer", "expires_in": 86400}'
                    % REAL_TOKEN
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "session=SECRET-SESSION; Path=/")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/v1/access_token"


@pytest.fixture
def token_server() -> Iterator[_TokenServer]:
    server = _TokenServer()
    server.thread.start()
    try:
        yield server
    finally:
        server.server.shutdown()
        server.server.server_close()
        server.thread.join(timeout=2.0)


def test_a_cassette_recorded_from_a_reddit_style_handshake_holds_no_credential(
    token_server: _TokenServer, tmp_path: Path
) -> None:
    path = tmp_path / "reddit.yaml"

    with make_vcr(True).use_cassette(str(path)):
        response = requests.post(
            token_server.url,
            auth=("client-id-1234", "client-secret-5678"),
            headers={"Cookie": "loid=SECRET-LOID"},
            data={"grant_type": "client_credentials"},
            timeout=5,
        )

    # What the live exchange really carried: the guard has something to scrub.
    assert token_server.seen_authorization[0] is not None
    assert response.json()["access_token"] == REAL_TOKEN
    text = path.read_text()
    assert_cassette_holds_no_credential(yaml.safe_load(text), text)
    assert ACCESS_TOKEN_PLACEHOLDER in text, "the token must be replaced, not dropped"
    for secret in (REAL_TOKEN, "SECRET-SESSION", "SECRET-LOID", "client-secret-5678"):
        assert secret not in text


def assert_cassette_holds_no_credential(cassette: dict[str, Any], text: str) -> None:
    """Fail if the parsed cassette carries a credential header or a real token value."""
    forbidden = {"authorization", "cookie", "set-cookie"}
    for interaction in cassette["interactions"]:
        for side in ("request", "response"):
            names = {name.lower() for name in interaction[side]["headers"]}
            assert not names & forbidden, f"{side} headers carry {names & forbidden}"
    for value in _access_token_values(text):
        assert value == ACCESS_TOKEN_PLACEHOLDER, f"unscrubbed access_token: {value!r}"


def _access_token_values(text: str) -> list[str]:
    import re

    return re.findall(r'access_token\\?"\s*:\s*\\?"([^"\\]*)', text)


def test_every_committed_cassette_holds_no_credential() -> None:
    cassettes = sorted(HTTP_CASSETTES_DIR.glob("*/*.yaml"))
    assert len(cassettes) >= 15, "the committed cassettes are the thing under inspection"

    for path in cassettes:
        text = path.read_text()
        assert_cassette_holds_no_credential(yaml.safe_load(text), text)


def test_the_credential_check_fails_on_a_cassette_that_holds_one() -> None:
    leaky = {
        "interactions": [
            {
                "request": {"headers": {"Authorization": ["Basic abc"]}},
                "response": {"headers": {}},
            }
        ]
    }
    with pytest.raises(AssertionError, match="authorization"):
        assert_cassette_holds_no_credential(leaky, "")
    with pytest.raises(AssertionError, match="unscrubbed access_token"):
        assert_cassette_holds_no_credential(
            {"interactions": []}, '{"access_token": "abc123"}'
        )
