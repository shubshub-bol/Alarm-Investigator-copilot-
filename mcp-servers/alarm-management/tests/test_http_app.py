"""The HTTP wrapper: /health is public, /mcp needs the MCP bearer token."""

from starlette.testclient import TestClient

from alarm_api_client import ClientSettings
from alarm_mcp.app import create_http_app
from alarm_mcp.config import McpSettings

MCP_TOKEN = "mcp-test-token"


def make_client() -> TestClient:
    settings = McpSettings(auth_token=MCP_TOKEN, allowed_hosts=["testserver"])
    api_settings = ClientSettings(base_url="http://alarm-api.invalid", token="unused")
    return TestClient(create_http_app(settings, api_settings=api_settings))


def test_health_is_public():
    with make_client() as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["tools"] == 13


def test_mcp_endpoint_rejects_missing_token():
    with make_client() as client:
        r = client.post("/mcp", json={})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_mcp_endpoint_rejects_wrong_token():
    with make_client() as client:
        r = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_mcp_endpoint_accepts_right_token():
    with make_client() as client:
        r = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    assert r.status_code != 401  # past auth; the MCP layer then judges the (empty) message
