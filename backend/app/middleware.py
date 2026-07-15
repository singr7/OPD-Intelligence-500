"""Request-scoped middleware: request id + audit actor binding.

The actor is read from the JWT here rather than in each route, so a write from
*any* code path in the request — route, service, background-safe helper — is
attributed to the same person. Auth itself is still enforced per route by
`app.auth.rbac`; this middleware only records identity, it never grants access.
An unreadable or absent token simply leaves the system actor in place.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.audit import Actor, acting_as
from app.auth.tokens import TokenError, decode_token
from app.config import Settings
from app.models.enums import Role

REQUEST_ID_HEADER = "X-Request-ID"


def _client_ip(request: Request) -> str | None:
    # Caddy terminates TLS and forwards; trust its header on the pilot box, fall
    # back to the socket peer. Revisit if anything else ever fronts the API.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _actor_from_request(request: Request, settings: Settings, request_id: str) -> Actor:
    ip = _client_ip(request)
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return Actor(request_id=request_id, ip=ip)

    try:
        claims = decode_token(header[7:], settings, expected_type="access")
    except TokenError:
        # Not this layer's job to reject — the route's auth dependency will.
        return Actor(request_id=request_id, ip=ip)

    try:
        role = Role(claims.get("role"))
    except ValueError:
        role = None

    return Actor(
        id=uuid.UUID(claims["sub"]),
        role=role,
        label=claims.get("name") or claims.get("sub", "unknown"),
        request_id=request_id,
        ip=ip,
    )


class AuditMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        actor = _actor_from_request(request, self.settings, request_id)

        request.state.request_id = request_id
        request.state.actor = actor

        with acting_as(actor):
            response = await call_next(request)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
