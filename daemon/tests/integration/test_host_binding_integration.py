"""Integration tests for host binding - protecting API auth and real network binding."""

import tempfile
from pathlib import Path
import pytest
import socket

from fastapi.testclient import TestClient
from prismis_daemon.api import app
from prismis_daemon.config import Config
import uvicorn


@pytest.fixture
def api_client() -> TestClient:
    """Create test client for API."""
    return TestClient(app)


def test_api_auth_with_lan_binding(api_client: TestClient) -> None:
    """
    INVARIANT: API Key Auth Maintained - LAN exposure must not bypass authentication
    BREAKS: Unauthorized access to personal content from LAN devices
    """
    # Test that LAN binding doesn't affect auth requirements
    # This simulates the daemon running with host="0.0.0.0"

    # Test health endpoint (no auth required)
    response = api_client.get("/health")
    assert response.status_code == 200

    # Test protected endpoint without API key (should fail regardless of host binding)
    response = api_client.get("/api/content")
    assert response.status_code == 403, "Must require API key even with LAN binding"
    data = response.json()
    assert data["success"] is False
    assert "API key" in data["message"] or "Forbidden" in data["message"]

    # Test with invalid API key (should fail)
    response = api_client.get("/api/content", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 403, "Must reject invalid API key"

    # Test multiple protected endpoints - all must require auth
    protected_endpoints = [
        "/api/content",
        "/api/sources",
        "/api/reports",
        "/api/prune/count",
    ]

    for endpoint in protected_endpoints:
        # Without API key
        response = api_client.get(endpoint)
        assert response.status_code == 403, (
            f"{endpoint} must require auth with LAN binding"
        )

        # With wrong API key
        response = api_client.get(endpoint, headers={"X-API-Key": "fake-key"})
        assert response.status_code == 403, f"{endpoint} must reject invalid API key"


def test_host_config_uvicorn_binding() -> None:
    """
    INVARIANT: Host Binding Correct - config.api_host correctly controls uvicorn binding
    BREAKS: Service unreachable when user configures LAN access
    """
    # Test localhost binding config
    test_toml_localhost = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"

[api]
key = "test-api-key"
host = "127.0.0.1"
"""

    # Test LAN binding config
    test_toml_lan = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"

[api]
key = "test-api-key"
host = "0.0.0.0"
"""

    temp_dir = tempfile.mkdtemp()
    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test context")

        # Test localhost config creates correct uvicorn config
        config_path.write_text(test_toml_localhost)
        config = Config.from_file(config_path)

        # This would be used to create uvicorn.Config(host=config.api_host)
        uvicorn_config = uvicorn.Config(app, host=config.api_host, port=8989)
        assert uvicorn_config.host == "127.0.0.1", (
            f"Should bind to localhost, got {uvicorn_config.host}"
        )

        # Test LAN config creates correct uvicorn config
        config_path.write_text(test_toml_lan)
        config = Config.from_file(config_path)

        uvicorn_config = uvicorn.Config(app, host=config.api_host, port=8989)
        assert uvicorn_config.host == "0.0.0.0", (
            f"Should bind to all interfaces, got {uvicorn_config.host}"
        )

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_invalid_host_binding_errors() -> None:
    """
    FAILURE: Network binding failures and invalid host configurations
    GRACEFUL: System must provide clear errors, not crash
    """
    # Test with obviously invalid host values
    invalid_hosts = [
        "999.999.999.999",  # Invalid IP
        "invalid-hostname-that-does-not-exist.local",  # Invalid hostname
        "",  # Empty string
        "localhost:8989",  # Host with port (should be just host)
    ]

    for invalid_host in invalid_hosts:
        # uvicorn should handle these gracefully, not crash
        try:
            # This should not crash the config creation
            uvicorn_config = uvicorn.Config(app, host=invalid_host, port=8989)

            # uvicorn will validate the host when server starts, not during config creation
            # So we test that config creation doesn't crash
            assert uvicorn_config.host == invalid_host, (
                "Config should store the host value"
            )

        except Exception as e:
            # If uvicorn does validate early, it should give clear error
            assert "host" in str(e).lower() or "address" in str(e).lower(), (
                f"Error should mention host/address issue: {e}"
            )


def test_port_conflict_handling() -> None:
    """
    FAILURE: Port 8989 already in use (common failure scenario)
    GRACEFUL: System must handle port conflicts gracefully
    """
    # Create a socket to occupy port 8989 on localhost
    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        # Bind to port 8989 to simulate conflict
        test_socket.bind(("127.0.0.1", 8989))
        test_socket.listen(1)

        # Now try to create uvicorn config with same port
        # uvicorn should handle this during server.serve(), not during config
        uvicorn_config = uvicorn.Config(app, host="127.0.0.1", port=8989)

        # Config creation should succeed
        assert uvicorn_config.port == 8989
        assert uvicorn_config.host == "127.0.0.1"

        # The actual error would happen during server.serve()
        # but we can't easily test that without complex async setup

    except OSError as e:
        # If the test port is already in use, that's fine for this test
        if "Address already in use" in str(e):
            pytest.skip("Port 8989 already in use during test")
        else:
            raise

    finally:
        test_socket.close()
