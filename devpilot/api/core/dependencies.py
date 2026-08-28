from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import HTTPConnection

from devpilot.api.core.config import Principal
from devpilot.api.core.security import bearer_scheme
from devpilot.api.services.control_plane import ControlPlaneService


def authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = request.app.state.api_settings.tokens.get(credentials.credentials)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def get_control_plane(connection: HTTPConnection) -> ControlPlaneService:
    return connection.app.state.control_plane


AuthenticatedPrincipal = Annotated[Principal, Depends(authenticate)]
ControlPlaneDependency = Annotated[ControlPlaneService, Depends(get_control_plane)]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        description="Stable key reused only while the outcome of this exact command is unknown",
        examples=["18cdd3fe-e2ad-45b2-96d7-e947e25f65c8"],
    ),
]
