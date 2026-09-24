"""A requests transport that holds every call inside one wall-clock deadline."""

import time
from collections.abc import Mapping

import requests
from requests.adapters import HTTPAdapter


class DeadlineAdapter(HTTPAdapter):
    """A transport adapter holding every request inside one wall-clock deadline.

    prawcore computes its 16-second default at import time and binds it as a default
    argument value, so neither setting its environment variable at runtime nor replacing
    the constant reaches a call made afterwards. The transport is the only remaining
    place a caller's budget can be imposed on the request itself. The deadline spans
    every request made until it is rearmed, because one logical operation costs several
    calls: the access token, then the subreddit, then its comments.

    What this guarantees, exactly. No request starts after the deadline — that refusal
    is absolute. Each socket operation is capped at whatever is left, because requests
    turns a float timeout into a urllib3 Timeout with connect and read both set to it,
    so a peer that accepts and never answers is cut off at the budget. What it does not
    guarantee is total elapsed time: connect and read are separate caps rather than one,
    and urllib3's read timeout measures the gap between reads rather than the whole
    response, so a peer trickling bytes can outlive the budget.
    """

    def __init__(self, budget: float) -> None:
        super().__init__()
        self._deadline = time.monotonic() + budget

    def rearm(self, budget: float) -> None:
        """Start a fresh deadline, for a long-lived client beginning a new operation."""
        self._deadline = time.monotonic() + budget

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: str | tuple[str, str] | None = None,
        proxies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Send a request bounded by whatever is left of the deadline."""
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.ConnectTimeout(
                "request deadline exhausted", request=request
            )
        if not isinstance(timeout, float | int) or timeout > remaining:
            timeout = remaining
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


def deadline_session(budget: float) -> requests.Session:
    """Build a requests session whose every call shares one wall-clock budget.

    Args:
        budget: Seconds allowed across all requests until the adapter is rearmed

    Returns:
        A session mounted on one deadline-bounded adapter for both schemes
    """
    session = requests.Session()
    adapter = DeadlineAdapter(budget)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
