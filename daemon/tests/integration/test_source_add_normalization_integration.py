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
