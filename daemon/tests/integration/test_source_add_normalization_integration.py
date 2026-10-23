"""Adding a source through the API stores one URL per source, however it was typed (#65).

Invariant protected:
  - the daemon is the one place protocol URLs are expanded, so `youtube://@h`,
    `youtube://@h/` and ` youtube://@h ` are one source, and a playlist id is refused
    rather than stored as a channel that fetches nothing

YouTube validation is format-only, so nothing here touches the network. Real API, real
Storage, real validator.
"""

from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

_HEADERS = {"X-API-Key": TEST_API_KEY}


def test_spellings_of_one_channel_are_one_source(test_db) -> None:
    """
    INVARIANT: Trailing slash and surrounding whitespace do not create a second source
    BREAKS: The same channel is fetched twice under two rows, doubling its items
    """
    client = TestClient(app)
    ids = set()
    for typed in (
        "youtube://@prismistest",
        "youtube://@prismistest/",
        " youtube://@prismistest ",
    ):
        response = client.post(
            "/api/sources", json={"url": typed, "type": "youtube"}, headers=_HEADERS
        )
        assert response.status_code == 200, f"{typed!r}: {response.text}"
        ids.add(response.json()["data"]["id"])

    urls = [s["url"] for s in Storage().get_all_sources()]
    assert urls == ["https://www.youtube.com/@prismistest"], urls
    assert len(ids) == 1, f"three spellings produced {len(ids)} source ids"


def test_a_playlist_id_is_refused_by_name(test_db) -> None:
    """
    INVARIANT: youtube://PL... is rejected as a playlist, and nothing is stored
    BREAKS: It is stored as /@PL... or /channel/PL..., passes validation, and every
            fetch cycle fails on a channel that does not exist
    """
    response = TestClient(app).post(
        "/api/sources",
        json={"url": "youtube://PLabc123", "type": "youtube"},
        headers=_HEADERS,
    )

    assert response.status_code != 200, response.text
    assert "playlist" in response.text.lower(), response.text
    assert Storage().get_all_sources() == []


def test_update_rejects_a_playlist_id_without_changing_the_stored_source(
    test_db,
) -> None:
    """
    INVARIANT: update_source's URL-change branch validates before writing, the same as
               add_source — both call the shared _validate_source_with_timeout helper
    BREAKS: update_source stops validating the new URL (e.g. the helper extraction wires
            only add_source's call site), so a source is silently repointed at a URL that
            fails the same check add_source enforces, and every fetch cycle after fails on
            a channel that does not exist
    """
    client = TestClient(app)
    created = client.post(
        "/api/sources",
        json={"url": "youtube://@prismistest", "type": "youtube"},
        headers=_HEADERS,
    )
    assert created.status_code == 200, created.text
    source_id = created.json()["data"]["id"]

    response = client.patch(
        f"/api/sources/{source_id}",
        json={"url": "youtube://PLabc123", "type": "youtube"},
        headers=_HEADERS,
    )

    assert response.status_code != 200, response.text
    assert "playlist" in response.text.lower(), response.text
    urls = [s["url"] for s in Storage().get_all_sources()]
    assert urls == ["https://www.youtube.com/@prismistest"], urls


def test_a_bare_custom_url_is_stored_as_the_channel_handle(test_db) -> None:
    """
    INVARIANT: youtube.com/DwarkeshPatel (the browser-bar form) is accepted and stored as
               https://www.youtube.com/@DwarkeshPatel, named @DwarkeshPatel
    BREAKS: the validator refuses the form YouTube itself serves, or stores the bare
            form so the same channel can exist twice under two spellings
    """
    import httpx

    from prismis_daemon.api import get_validator
    from prismis_daemon.validator import SourceValidator

    page = (
        '<link rel="canonical" href="https://www.youtube.com/channel/UCXl4i9dYBrFOabk0xGmbkRA">'
        '"vanityChannelUrl":"http://www.youtube.com/@DwarkeshPatel"'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    app.dependency_overrides[get_validator] = lambda: SourceValidator(
        transport=transport
    )
    try:
        response = TestClient(app).post(
            "/api/sources",
            json={"url": "https://www.youtube.com/DwarkeshPatel", "type": "youtube"},
            headers=_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_validator, None)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["url"] == "https://www.youtube.com/@DwarkeshPatel"
    sources = Storage().get_all_sources()
    assert [(s["url"], s["name"]) for s in sources] == [
        ("https://www.youtube.com/@DwarkeshPatel", "@DwarkeshPatel")
    ], sources
