"""Integration tests for the /assets mount on the real FastAPI app."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from prismis_daemon import api

ASSETS = Path(api.__file__).parent / "static" / "assets"
INDEX_HTML = (Path(api.__file__).parent / "static" / "index.html").read_bytes()


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(api.app)


@pytest.mark.parametrize("name", ["marked.umd.js", "purify.min.js"])
def test_vendored_script_is_served_byte_for_byte(
    api_client: TestClient, name: str
) -> None:
    """
    INVARIANT: /assets serves each vendored script as JavaScript with the recorded sha256
    BREAKS: the web view loads no markdown renderer and every detail panel fails
    """
    manifest = json.loads((ASSETS / "VENDORED.json").read_text())
    response = api_client.get(f"/assets/{name}")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert hashlib.sha256(response.content).hexdigest() == manifest[name]["sha256"]


def test_unknown_asset_is_404_not_the_spa(api_client: TestClient) -> None:
    """
    INVARIANT: an unknown /assets path is a 404, never index.html
    BREAKS: a script tag whose file is missing receives HTML and fails as a syntax error
    """
    response = api_client.get("/assets/missing.js")
    assert response.status_code == 404
    assert response.content != INDEX_HTML


def test_non_api_path_still_serves_index_html(api_client: TestClient) -> None:
    response = api_client.get("/anything")
    assert response.status_code == 200
    assert response.content == INDEX_HTML


def test_serve_spa_refuses_assets_prefix_like_api_and_audio(
    api_client: TestClient,
) -> None:
    """The catch-all itself answers 404 for assets/ paths, whatever the mount does."""
    route = next(
        r
        for r in api.app.routes
        if getattr(r, "path", None) == "/{full_path:path}"
        and "GET" in getattr(r, "methods", set())
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(route.endpoint("assets/anything.js"))  # type: ignore[attr-defined]
    assert excinfo.value.status_code == 404
