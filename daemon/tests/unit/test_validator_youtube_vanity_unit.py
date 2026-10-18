"""A YouTube channel pasted from the browser bar can be its bare custom URL
(youtube.com/DwarkeshPatel). The validator used to refuse it as an invalid format
although YouTube serves it; now it reads the page's canonical channel link and
hands back the handle to store, so the source is kept in the same @handle form as
every other channel. A bare path that is not a channel (youtube.com/feed) has no
canonical channel link and is still refused. YouTube is the third-party boundary,
so its pages are served through an httpx.MockTransport; the client, parser and
error path are the real ones."""

from __future__ import annotations

import httpx

from prismis_daemon.validator import SourceValidator

_CHANNEL_PAGE = (
    '<html><head><link rel="canonical" href="https://www.youtube.com/channel/UCXl4i9dYBrFOabk0xGmbkRA">'
    '</head><body>"vanityChannelUrl":"http://www.youtube.com/@DwarkeshPatel"</body></html>'
)
_NOT_A_CHANNEL_PAGE = "<html><head><title>YouTube</title></head><body></body></html>"


def _youtube(pages: dict[str, str]) -> tuple[SourceValidator, list[str]]:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, text=pages[str(request.url)])

    return SourceValidator(transport=httpx.MockTransport(handler)), fetched


def test_bare_custom_url_resolves_to_the_channel_handle() -> None:
    validator, fetched = _youtube({"https://www.youtube.com/DwarkeshPatel": _CHANNEL_PAGE})

    ok, message, metadata = validator.validate_source(
        "https://www.youtube.com/DwarkeshPatel", "youtube"
    )

    assert (ok, message) == (True, None)
    assert metadata == {"canonical_url": "https://www.youtube.com/@DwarkeshPatel"}
    assert fetched == ["https://www.youtube.com/DwarkeshPatel"]


def test_channel_without_a_handle_resolves_to_its_channel_id() -> None:
    page = '<link rel="canonical" href="https://www.youtube.com/channel/UCXl4i9dYBrFOabk0xGmbkRA">'
    validator, _ = _youtube({"https://www.youtube.com/SomeOldCustomName": page})

    ok, _, metadata = validator.validate_source(
        "https://www.youtube.com/SomeOldCustomName", "youtube"
    )

    assert ok is True
    assert metadata == {"canonical_url": "https://www.youtube.com/channel/UCXl4i9dYBrFOabk0xGmbkRA"}


def test_bare_path_that_is_not_a_channel_is_still_refused() -> None:
    validator, _ = _youtube({"https://www.youtube.com/feed": _NOT_A_CHANNEL_PAGE})

    ok, message, metadata = validator.validate_source("https://www.youtube.com/feed", "youtube")

    assert ok is False
    assert message == "Invalid YouTube channel URL format"
    assert metadata is None


def test_handle_and_channel_forms_are_accepted_without_a_fetch() -> None:
    validator, fetched = _youtube({})
    for url in (
        "https://www.youtube.com/@DwarkeshPatel",
        "https://www.youtube.com/channel/UCXl4i9dYBrFOabk0xGmbkRA",
    ):
        assert validator.validate_source(url, "youtube") == (True, None, None)
    assert fetched == []


def test_unreachable_page_is_a_validation_error_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    validator = SourceValidator(transport=httpx.MockTransport(handler))

    ok, message, metadata = validator.validate_source(
        "https://www.youtube.com/DwarkeshPatel", "youtube"
    )

    assert ok is False
    assert message is not None and "could not reach" in message.lower()
    assert metadata is None
