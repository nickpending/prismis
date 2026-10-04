"""Unit tests for the shared article extractor (SC-2).

fetchers/rss.py's `_extract_full_content` used to be the only place that called
trafilatura's `fetch_url`/`extract`; SC-2 moves that call into one helper both the
RSS and Reddit fetchers call, and title-only-reasons SC-2 makes it report fetch
failure, empty extraction and success as three outcomes. Per Principle I, the HTTP
side of this is exercised against a real local server, not a mock.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismis_daemon.article_extractor import (
    ArticleResult,
    BlockedAddressError,
    _is_public,
    _resolve_guarded,
    extract_article,
)

_EMPTY_HTML = (
    b"<!doctype html><html><head><title>x</title></head><body></body></html>"
)

_ARTICLE_HTML = b"""<!doctype html>
<html><head><title>A Real Article</title></head>
<body>
<article>
<h1>A Real Article</h1>
<p>This is the first paragraph of a genuine article, long enough for trafilatura's
extraction heuristics to recognize it as the main content block rather than
boilerplate chrome around it.</p>
<p>A second paragraph continues the thought, adding enough additional real prose
that the extracted text is unambiguously the article body and not a snippet.</p>
</article>
</body></html>
"""


def _fetch_local(url: str) -> ArticleResult:
    """`extract_article` with the loopback test server allowed, as the suite's
    materialized config allows it for the fetchers."""
    return extract_article(url, allowed_private_hosts=("127.0.0.1",))


@pytest.fixture
def article_server_with_redirect() -> Iterator[tuple[str, http.server.ThreadingHTTPServer]]:
    """A local HTTP server serving a real article page, a page with nothing
    extractable, a 429 and a 404 -- the real boundary `extract_article` fetches, not
    a stand-in for it."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            pages = {"/article": _ARTICLE_HTML, "/empty": _EMPTY_HTML}
            if self.path in pages:
                body = pages[self.path]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/rate-limited":
                self.send_error(429)
            elif self.path.startswith("/redirect-to-file"):
                self.send_response(302)
                self.send_header("Location", self.server.redirect_target)  # type: ignore[attr-defined]
                self.end_headers()
            else:
                self.send_error(404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.redirect_target = ""  # type: ignore[attr-defined]
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.fixture
def article_server(
    article_server_with_redirect: tuple[str, http.server.ThreadingHTTPServer],
) -> str:
    return article_server_with_redirect[0]


def test_extract_article_returns_extracted_text_for_a_real_page(
    article_server: str,
) -> None:
    """
    BREAKS: A helper that only fetches without also calling `extract` would return
    raw HTML (including the `<article>`/`<h1>` markup) instead of the extracted
    plain-text body, and one that never reports `extracted` leaves callers unable
    to tell success from the two failures.
    """
    result = _fetch_local(f"{article_server}/article")

    assert result.outcome == "extracted"
    assert result.text is not None
    assert "first paragraph of a genuine article" in result.text
    assert "<article>" not in result.text
    assert "<h1>" not in result.text


def test_extract_article_reports_the_status_of_a_429_and_a_404(
    article_server: str,
) -> None:
    """
    BREAKS: Collapsing a failed download into one None (or into `empty`) makes a
    rate-limited site and a missing page indistinguishable from a page with no
    text; dropping the status from `detail` loses what the reason exists to say.
    """
    limited = _fetch_local(f"{article_server}/rate-limited")
    missing = _fetch_local(f"{article_server}/does-not-exist")

    assert (limited.text, limited.outcome, limited.detail) == (
        None,
        "fetch_failed",
        "HTTP 429",
    )
    assert (missing.text, missing.outcome, missing.detail) == (
        None,
        "fetch_failed",
        "HTTP 404",
    )


def test_extract_article_reports_a_fetched_page_with_no_text_as_empty(
    article_server: str,
) -> None:
    """
    BREAKS: Treating "fetched but nothing extractable" as a failed fetch blames the
    network for a page that answered, so the stored reason points at the wrong cause.
    """
    result = _fetch_local(f"{article_server}/empty")

    assert result.text is None
    assert result.outcome == "empty"


def test_extract_article_reports_no_response_when_nothing_listens() -> None:
    """A URL nothing listens on is a failed fetch with `no response`, not a raised
    exception and not `empty`. Port 1 on loopback refuses the connection."""
    result = _fetch_local("http://127.0.0.1:1/nothing")

    assert result.text is None
    assert result.outcome == "fetch_failed"
    assert result.detail == "no response"


def test_extract_article_outcomes_are_pairwise_distinct(article_server: str) -> None:
    """SC-2's guard: no two of the five inputs share an (outcome, detail) pair they
    should not -- the two HTTP failures differ by detail, the rest by outcome."""
    results = [
        _fetch_local(f"{article_server}/rate-limited"),
        _fetch_local(f"{article_server}/does-not-exist"),
        _fetch_local("http://127.0.0.1:1/nothing"),
        _fetch_local(f"{article_server}/empty"),
        _fetch_local(f"{article_server}/article"),
    ]

    pairs = [(r.outcome, r.detail) for r in results]
    assert len(set(pairs)) == len(pairs)


def test_extract_article_refuses_a_file_url_and_never_reads_the_file(
    tmp_path: Path,
) -> None:
    """
    A feed entry or Reddit post can carry any URL; only http(s) may be fetched.
    BREAKS: an opener that handles `file://` reads local disk into item content.
    """
    secret = tmp_path / "secret.html"
    secret.write_text(_ARTICLE_HTML.decode())

    result = extract_article(secret.as_uri())

    assert result.text is None
    assert result.outcome == "fetch_failed"
    assert "first paragraph of a genuine article" not in (result.text or "")


def test_extract_article_refuses_a_redirect_to_a_file_url(
    article_server_with_redirect: tuple[str, http.server.ThreadingHTTPServer],
    tmp_path: Path,
) -> None:
    """A server answering with a redirect to `file://` must not get the file read."""
    base, server = article_server_with_redirect
    secret = tmp_path / "secret.html"
    secret.write_text(_ARTICLE_HTML.decode())
    server.redirect_target = secret.as_uri()  # type: ignore[attr-defined]

    result = _fetch_local(f"{base}/redirect-to-file")

    assert result.text is None
    assert result.outcome == "fetch_failed"


@pytest.fixture
def recording_server() -> Iterator[tuple[str, int, list[str], http.server.ThreadingHTTPServer]]:
    """A loopback server that records every request path it receives and serves
    `/article`, plus `/redirect` to whatever `server.redirect_target` holds."""
    hits: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", self.server.redirect_target)  # type: ignore[attr-defined]
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(_ARTICLE_HTML)))
            self.end_headers()
            self.wfile.write(_ARTICLE_HTML)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.redirect_target = ""  # type: ignore[attr-defined]
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", port, hits, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_extract_article_refuses_loopback_and_makes_no_request(
    recording_server: tuple[str, int, list[str], http.server.ThreadingHTTPServer],
    host: str,
) -> None:
    """
    SC-1: with an empty allow list a loopback URL is refused before any connection.
    BREAKS: an extractor with no address guard fetches the page, the server records
    the request and the result is `extracted`.
    """
    _, port, hits, _server = recording_server

    result = extract_article(f"http://{host}:{port}/article")

    assert (result.text, result.outcome, result.detail) == (
        None,
        "fetch_failed",
        "blocked address",
    )
    assert hits == []


def test_extract_article_refuses_a_redirect_to_a_host_not_allowed(
    recording_server: tuple[str, int, list[str], http.server.ThreadingHTTPServer],
) -> None:
    """
    SC-2: the first hop is allowed (127.0.0.1) and redirects to `localhost`, which
    is not allowed; the redirect hop is refused and its target is never requested.
    BREAKS: a guard that checks only the first URL follows the redirect and
    records `/target` on the server.
    """
    _, port, hits, server = recording_server
    server.redirect_target = f"http://localhost:{port}/target"  # type: ignore[attr-defined]

    result = extract_article(
        f"http://127.0.0.1:{port}/redirect", allowed_private_hosts=("127.0.0.1",)
    )

    assert (result.outcome, result.detail) == ("fetch_failed", "blocked address")
    assert hits == ["/redirect"]


def test_extract_article_fetches_an_allowed_private_host(
    recording_server: tuple[str, int, list[str], http.server.ThreadingHTTPServer],
) -> None:
    """SC-4: naming the host in the allow list lets the same page through.
    BREAKS: an allow list that is ignored leaves this `blocked address`."""
    _, port, hits, _server = recording_server

    result = extract_article(
        f"http://127.0.0.1:{port}/article", allowed_private_hosts=("127.0.0.1",)
    )

    assert result.outcome == "extracted"
    assert hits == ["/article"]


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "fd00::1",
    ],
)
def test_guard_refuses_every_non_public_address(address: str) -> None:
    """
    SC-3: each listed non-public address is refused by the check the connection
    uses, whichever address family it arrives in.
    BREAKS: a check on IPv4 only, or one that misses carrier-grade NAT or an
    IPv4-mapped IPv6 loopback, lets one of these through.
    """
    assert _is_public(address) is False
    with pytest.raises(BlockedAddressError):
        _resolve_guarded(address, 80, frozenset())


def test_guard_allows_a_public_address_and_returns_the_address_it_approved() -> None:
    """SC-3: a public address passes, and the socket address handed back for the
    connection is the one that was checked, not a second lookup.
    BREAKS: a guard that refuses everything, or one that returns a different
    address than it approved."""
    assert _is_public("93.184.216.34") is True

    info = _resolve_guarded("93.184.216.34", 80, frozenset())

    assert info[4] == ("93.184.216.34", 80)
