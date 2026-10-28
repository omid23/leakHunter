"""API-key authentication with two roles: viewers read and analyse, operators also approve or reject."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from leakhunter.config import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class Role(str, Enum):
    viewer = "viewer"
    operator = "operator"


@dataclass(frozen=True)
class Principal:
    role: Role
    actor: str


class AuthError(Exception):
    def __init__(self, status_code: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind
        self.message = message


def resolve_principal(api_key: str | None, settings: Settings) -> Principal:
    if not api_key:
        raise AuthError(401, "unauthorized", "missing X-API-Key header")
    if api_key in settings.operator_key_set:
        return Principal(Role.operator, f"operator:{api_key[:6]}")
    if api_key in settings.viewer_key_set:
        return Principal(Role.viewer, f"viewer:{api_key[:6]}")
    raise AuthError(401, "unauthorized", "invalid API key")


def require_role(role: Role) -> Callable[..., Awaitable[Principal]]:
    async def dependency(request: Request, api_key: str | None = Depends(api_key_header)) -> Principal:
        principal = resolve_principal(api_key, request.app.state.services.settings)
        if role is Role.operator and principal.role is not Role.operator:
            raise AuthError(403, "forbidden", "approving or rejecting actions requires an operator key")
        return principal

    return dependency
