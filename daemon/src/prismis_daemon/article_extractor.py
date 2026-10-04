"""The one full-article extractor, shared by every fetcher that turns a URL into
article text (SC-2).

fetchers/rss.py used to be the only caller of trafilatura's `fetch_url`/`extract`;
fetchers/reddit.py now needs the same extraction for a link post's outbound URL. This
module is that one call, so both fetchers share it instead of each carrying its own
copy that can drift.
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from trafilatura import extract
from trafilatura.utils import decode_file

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SECONDS = 30
_MAX_PAGE_BYTES = 20_000_000
_USER_AGENT = "Mozilla/5.0 (compatible; prismis-daemon)"

ArticleOutcome = Literal["extracted", "empty", "fetch_failed"]


@dataclass(frozen=True)
class ArticleResult:
    """What one extraction attempt found: the text, and which of three outcomes it was."""

    text: str | None
    outcome: ArticleOutcome
    detail: str = ""

    def as_fetch_outcome(self) -> dict[str, str]:
        """The `{"outcome", "detail"}` record fetchers store under `fetch_outcome`."""
        return {"outcome": self.outcome, "detail": self.detail}


class BlockedAddressError(OSError):
    """A connection was refused because its host resolves to a non-public address."""


def _is_public(address: str) -> bool:
    """Whether `address` is a globally routable IP: not loopback, private,
    link-local (169.254.169.254 included), carrier-grade NAT, unique-local or an
    IPv4-mapped form of any of those."""
    return ipaddress.ip_address(address.split("%", 1)[0]).is_global


def _resolve_guarded(
    host: str, port: int, allowed_hosts: frozenset[str]
) -> tuple[Any, ...]:
    """The one socket address to connect to for `host`, refused unless public.

    Resolves once and returns the first result, so the address checked is the
    address connected to -- a second lookup at connect time could answer
    differently (DNS rebinding). Every address the name resolves to must be public,
    so a name that also resolves to a private address is refused. A host named in
    `allowed_hosts` skips the check.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if host.lower() not in allowed_hosts and not all(
        _is_public(str(info[4][0])) for info in infos
    ):
        raise BlockedAddressError(f"blocked address: {host}")
    return tuple(infos[0])


def _guarded_connection(
    allowed_hosts: frozenset[str],
    address: tuple[str, int],
    timeout: object = None,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """`socket.create_connection` that connects only to the address it approved."""
    host, port = address
    family, socktype, proto, _canon, sockaddr = _resolve_guarded(
        host, port, allowed_hosts
    )
    sock = socket.socket(family, socktype, proto)
    try:
        # http.client passes socket's default-timeout sentinel when no timeout was
        # given; like socket.create_connection, leave the socket's default then.
        if isinstance(timeout, (int, float)):
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


def _guard(
    conn: http.client.HTTPConnection, allowed_hosts: frozenset[str]
) -> http.client.HTTPConnection:
    conn._create_connection = lambda address, timeout=None, source_address=None: (  # type: ignore[attr-defined]
        _guarded_connection(allowed_hosts, address, timeout, source_address)
    )
    return conn


def _http_only_opener(
    allowed_private_hosts: Iterable[str] = (),
) -> urllib.request.OpenerDirector:
    """An opener with http(s), redirect, proxy and error handling and no file/ftp/data.

    Every connection it opens -- the first request and each redirect hop -- goes
    through `_guarded_connection`, which refuses non-public addresses except for
    hosts named in `allowed_private_hosts`.
    """
    allowed = frozenset(h.lower() for h in allowed_private_hosts)

    class _HTTPConnection(http.client.HTTPConnection):
        def __init__(
            self,
            host: str,
            /,
            *,
            port: int | None = None,
            timeout: float = _DOWNLOAD_TIMEOUT_SECONDS,
            source_address: tuple[str, int] | None = None,
            blocksize: int = 8192,
        ) -> None:
            super().__init__(
                host,
                port=port,
                timeout=timeout,
                source_address=source_address,
                blocksize=blocksize,
            )
            _guard(self, allowed)

    class _HTTPSConnection(http.client.HTTPSConnection):
        def __init__(
            self,
            host: str,
            /,
            *,
            port: int | None = None,
            timeout: float = _DOWNLOAD_TIMEOUT_SECONDS,
            source_address: tuple[str, int] | None = None,
            blocksize: int = 8192,
            context: ssl.SSLContext | None = None,
        ) -> None:
            super().__init__(
                host,
                port=port,
                timeout=timeout,
                source_address=source_address,
                context=context,
                blocksize=blocksize,
            )
            _guard(self, allowed)

    class _HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
            return self.do_open(_HTTPConnection, req)

    class _HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
            return self.do_open(
                _HTTPSConnection,
                req,
                context=self._context,  # type: ignore[attr-defined]
            )

    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        _HTTPHandler(),
        _HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def extract_article(
    url: str, *, allowed_private_hosts: Iterable[str] = ()
) -> ArticleResult:
    """Fetch `url` and extract its main article text with trafilatura.

    Three outcomes: `extracted` (text set), `empty` (the page was fetched but
    trafilatura found nothing) and `fetch_failed` (`detail` is `HTTP <status>`
    when the server answered with an error status, `no response` when nothing
    came back, `blocked address` when the host (or a redirect hop) resolves to a
    non-public address and is not in `allowed_private_hosts`, or the exception
    class name). The caller owns its own fallback --
    RSS's (the feed entry's own content) differs from Reddit's (the link-only
    placeholder) -- so this never raises or chooses one.
    """
    try:
        # One request, no status retries: trafilatura's own downloader retries a 429
        # or 5xx with long backoff and then reports only None, which hides the status
        # this result exists to carry.
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        # The URL is untrusted (a feed entry link, a Reddit post url): only http(s)
        # is fetched, never `file://` or `ftp://`.
        if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
            return ArticleResult(None, "fetch_failed", "unsupported scheme")
        # A fresh opener per call: `urlopen` caches one global opener, and with it
        # the proxy environment of whichever call came first. Built from http(s)
        # handlers only, so a redirect cannot reach `ftp://` or `file://` either.
        opener = _http_only_opener(allowed_private_hosts)
        with opener.open(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_PAGE_BYTES + 1)
        if len(body) > _MAX_PAGE_BYTES:
            return ArticleResult(None, "fetch_failed", "page too large")
        html = decode_file(body)

        content = (
            extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            if html
            else None
        )
        if content:
            return ArticleResult(content, "extracted")
        return ArticleResult(None, "empty")

    except urllib.error.HTTPError as e:
        return ArticleResult(None, "fetch_failed", f"HTTP {e.code}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, BlockedAddressError):
            return ArticleResult(None, "fetch_failed", "blocked address")
        return ArticleResult(None, "fetch_failed", "no response")
    except Exception as e:
        logger.warning(f"Error extracting article from {url}: {e}")
        return ArticleResult(None, "fetch_failed", type(e).__name__)
