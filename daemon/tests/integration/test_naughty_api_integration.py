"""The sources API stores, lists and updates a source's naughty flag.

Invariant protected:
  - POST /api/sources stores `naughty` (false when absent), GET /api/sources lists it,
    and PATCH /api/sources/{id} changes it only when the body carries it; a body in
    the shape clients sent before the flag existed is accepted by both

Success criteria covered: C4.

Real API, real Storage, real validator. Every source here is a file source, whose
validation is format-only, so nothing touches the network.
"""

from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

_HEADERS = {"X-API-Key": TEST_API_KEY}
_URL = "https://example.com/CHANGELOG.md"


def _listed(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/sources", headers=_HEADERS)
    assert response.status_code == 200, response.text
    return {s["id"]: s for s in response.json()["data"]["sources"]}


def _patch(client: TestClient, source_id: str, body: dict) -> None:
    response = client.patch(f"/api/sources/{source_id}", json=body, headers=_HEADERS)
    assert response.status_code == 200, f"{body}: {response.text}"


def test_the_flag_is_stored_listed_and_changed_only_when_sent(test_db) -> None:
    """
    INVARIANT: naughty=true is stored and listed; PATCH naughty=false clears it; a PATCH
               carrying only a name changes the name and leaves the flag as it was,
               whichever value it holds
    BREAKS: a rename silently drops a source's browser user-agent and its feed starts
            failing on 403 again, or the flag cannot be set or cleared at all
    """
    client = TestClient(app)
    created = client.post(
        "/api/sources",
        json={"url": _URL, "type": "file", "naughty": True},
        headers=_HEADERS,
    )
    assert created.status_code == 200, created.text
    source_id = created.json()["data"]["id"]
    assert created.json()["data"]["naughty"] is True

    assert _listed(client)[source_id]["naughty"] is True

    _patch(client, source_id, {"naughty": False})
    assert _listed(client)[source_id]["naughty"] is False

    _patch(client, source_id, {"name": "Renamed while unmarked"})
    listed = _listed(client)[source_id]
    assert (listed["name"], listed["naughty"]) == ("Renamed while unmarked", False)

    _patch(client, source_id, {"naughty": True})
    _patch(client, source_id, {"name": "Renamed while marked"})
    listed = _listed(client)[source_id]
    assert (listed["name"], listed["naughty"]) == ("Renamed while marked", True)

    # A new connection reads what the API wrote: the flag is stored, not cached.
    storage = Storage(test_db)
    try:
        (stored,) = [s for s in storage.get_all_sources() if s["id"] == source_id]
    finally:
        storage.close()
    assert stored["naughty"] is True


def test_a_pre_change_client_shape_is_accepted_and_stores_unmarked(test_db) -> None:
    """
    INVARIANT: a POST carrying only url, type and name stores naughty false, and a PATCH
               in the url/type/name shape the TUI sends renames without touching the flag
    BREAKS: a TUI or CLI built before the flag existed can no longer add or rename a
            source, or a TUI rename unmarks a marked source
    """
    client = TestClient(app)
    plain = client.post(
        "/api/sources",
        json={"url": _URL, "type": "file", "name": "Changelog"},
        headers=_HEADERS,
    )
    assert plain.status_code == 200, plain.text
    plain_id = plain.json()["data"]["id"]
    assert _listed(client)[plain_id]["naughty"] is False

    marked = client.post(
        "/api/sources",
        json={"url": "https://example.com/NOTES.txt", "type": "file", "naughty": True},
        headers=_HEADERS,
    )
    assert marked.status_code == 200, marked.text
    marked_id = marked.json()["data"]["id"]

    _patch(
        client,
        marked_id,
        {"url": "https://example.com/NOTES.txt", "type": "file", "name": "Notes"},
    )
    listed = _listed(client)[marked_id]
    assert (listed["name"], listed["naughty"]) == ("Notes", True)
