import hmac

from fastapi import Request

from alarm_simulator.errors import ApiError


async def require_bearer_token(request: Request) -> None:
    """Router dependency: reject requests without the configured bearer token."""
    expected: str = request.app.state.settings.api_token
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    # compare_digest avoids leaking token contents through response timing
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token.strip(), expected):
        raise ApiError(401, "unauthorized", "Missing or invalid bearer token.", headers={"WWW-Authenticate": "Bearer"})
