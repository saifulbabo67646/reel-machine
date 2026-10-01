"""Per-call caller resolution.

The bearer token arrives in the request headers (streamable-http) or is implicit (stdio,
which is a local, trusted transport). Either way the caller id is what every engine call
is scoped to.
"""

from __future__ import annotations

from typing import Mapping

from ..core.errors import Unauthorized
from .tenants import TenantRegistry


def bearer_token(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            text = str(value).strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
            return text
    return ""


def resolve_caller(
    headers: Mapping[str, str] | None,
    registry: TenantRegistry,
    *,
    transport: str,
) -> str:
    token = bearer_token(headers)
    if token:
        tenant = registry.by_token(token)
        if tenant is None:
            raise Unauthorized(
                "the bearer token is not recognised",
                hint="check the token, or ask the deployment to register it",
            )
        return tenant.id
    if registry.requires_token:
        raise Unauthorized(
            "missing bearer token",
            hint="send `Authorization: Bearer <token>` on every request",
        )
    if transport == "stdio":
        return registry.default_caller
    raise Unauthorized(
        "this server has no tokens configured and refuses anonymous HTTP callers",
        hint="set REEL_MCP_TOKEN, or REEL_MCP_TENANTS with a callers list",
    )
