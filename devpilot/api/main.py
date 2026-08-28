from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from devpilot.api.core.config import ApiSettings
from devpilot.api.core.errors import install_exception_handlers
from devpilot.api.core.middleware import install_request_context
from devpilot.api.core.security import EventTicketStore, RateLimiter
from devpilot.api.services import ControlPlaneService
from devpilot.api.v1.router import router as v1_router
from devpilot.service import TaskService


def create_app(
    *,
    service: TaskService | None = None,
    settings: ApiSettings | None = None,
) -> FastAPI:
    """Create the API and wire infrastructure without embedding route logic."""

    api_settings = settings or ApiSettings.from_env()
    task_service = service or TaskService()
    owns_service = service is None
    tickets = EventTicketStore(api_settings.ticket_ttl_seconds)
    limiter = RateLimiter()
    control_plane = ControlPlaneService(task_service, tickets, limiter)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_service:
            task_service.close()

    app = FastAPI(
        title="DevPilot Control API",
        summary="Human-in-the-loop control plane for DevPilot tasks",
        description=(
            "Phase 4 REST and WebSocket API. Authenticate in this page with a bearer token, "
            "then create tasks, inspect durable execution evidence, and issue revision-bound "
            "control commands. Chat messages never execute control operations."
        ),
        version="0.4.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "DevPilot maintainers"},
        license_info={"name": "Repository license"},
    )
    app.state.task_service = task_service
    app.state.api_settings = api_settings
    app.state.control_plane = control_plane
    app.state.event_tickets = tickets
    app.state.rate_limiter = limiter

    if api_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(api_settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )

    install_request_context(app)
    install_exception_handlers(app)
    # The code is versioned internally while /api remains the frozen Phase 5 contract.
    app.include_router(v1_router, prefix="/api")
    return app


__all__ = ["create_app"]
