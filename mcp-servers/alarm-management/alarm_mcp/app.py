"""Streamable-HTTP app: MCP endpoint at /mcp (bearer-token protected) plus a public /health."""

import hmac
import json
import logging

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from alarm_api_client import AlarmApiClient, ClientSettings
from alarm_mcp import __version__
from alarm_mcp.config import McpSettings
from alarm_mcp.server import build_server

log = logging.getLogger("alarm_mcp")


class BearerAuthMiddleware:
    """Reject requests without the MCP bearer token (except exempt paths such as /health)."""

    def __init__(self, app: ASGIApp, token: str, exempt_paths: frozenset[str] = frozenset({"/health"})):
        self.app = app
        self.expected = f"Bearer {token}".encode()
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in self.exempt_paths:
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                body = json.dumps({"error": {"code": "unauthorized", "message": "Missing or invalid MCP token."}})
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": body.encode()})
                return
        await self.app(scope, receive, send)


def create_http_app(settings: McpSettings, api_settings: ClientSettings | None = None) -> Starlette:
    if settings.auth_token is None and not settings.allow_unauthenticated:
        raise RuntimeError("MCP_AUTH_TOKEN is not set. Set it, or MCP_ALLOW_UNAUTHENTICATED=true for local use.")
    api_settings = api_settings or ClientSettings()  # fails fast if ALARM_API_TOKEN is missing
    server = build_server(api_client_factory=lambda: AlarmApiClient(api_settings))

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        host=settings.host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[f"http://{h}" for h in settings.allowed_hosts],
        ),
    )

    async def health(_request: Request) -> JSONResponse:
        tools = await server.list_tools()
        return JSONResponse(
            {"status": "ok", "service": "alarm-mcp-server", "version": __version__, "tools": len(tools)}
        )

    app.router.routes.append(Route("/health", health, methods=["GET"]))
    if settings.auth_token is not None:
        app.add_middleware(BearerAuthMiddleware, token=settings.auth_token.get_secret_value())
    else:
        log.warning("MCP endpoint is running WITHOUT authentication")
    return app
