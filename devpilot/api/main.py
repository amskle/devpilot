from __future__ import annotations

import asyncio
import logging
import multiprocessing
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from devpilot.api.core.config import ApiSettings
from devpilot.api.core.errors import install_exception_handlers
from devpilot.api.core.middleware import install_request_context
from devpilot.api.core.security import (
    EventTicketStore,
    RateLimiter,
    RedisEventTicketStore,
    RedisRateLimiter,
)
from devpilot.api.services import ControlPlaneService
from devpilot.api.v1.router import router as v1_router
from devpilot.events import OutboxRelay, RedisStreamConsumer, RedisStreamTransport
from devpilot.service import TaskService


LOGGER = logging.getLogger("devpilot.api")


def create_app(
    *,
    service: TaskService | None = None,
    settings: ApiSettings | None = None,
    redis_client: Any | None = None,
) -> FastAPI:
    """Create the API and wire infrastructure without embedding route logic."""

    api_settings = settings or ApiSettings.from_env()
    if api_settings.uses_default_token:
        LOGGER.warning(
            "SECURITY WARNING: using the public development administrator token "
            "'devpilot-local'; configure DEVPILOT_API_TOKENS before sharing this API"
        )
    if (
        api_settings.redis_url is None
        and multiprocessing.parent_process() is not None
    ):
        raise RuntimeError("worker processes require DEVPILOT_REDIS_URL")
    task_service = service or TaskService()
    owns_service = service is None
    owns_redis = False
    relay: OutboxRelay | None = None
    live_events: RedisStreamConsumer | None = None
    if api_settings.redis_url is not None:
        if redis_client is None:
            import redis

            redis_client = redis.Redis.from_url(
                api_settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            owns_redis = True
        tickets = RedisEventTicketStore(
            redis_client,
            ttl_seconds=api_settings.ticket_ttl_seconds,
            key_prefix=api_settings.redis_key_prefix,
        )
        limiter = RedisRateLimiter(
            redis_client, key_prefix=api_settings.redis_key_prefix
        )
        stream_prefix = f"{api_settings.redis_key_prefix.rstrip(':')}:events"
        transport = RedisStreamTransport(
            redis_client, stream_prefix=stream_prefix
        )
        live_events = RedisStreamConsumer(
            redis_client, stream_prefix=stream_prefix
        )
        relay = OutboxRelay(
            task_service.control,
            transport,
            relay_id=f"api-{uuid.uuid4().hex}",
        )
    else:
        tickets = EventTicketStore(api_settings.ticket_ttl_seconds)
        limiter = RateLimiter()
    control_plane = ControlPlaneService(
        task_service,
        tickets,
        limiter,
        live_events=live_events,
        repository_roots=api_settings.repository_roots,
    )

    async def run_relay(stop: asyncio.Event) -> None:
        assert relay is not None
        while not stop.is_set():
            try:
                result = await asyncio.to_thread(relay.run_once)
            except Exception:
                LOGGER.exception("outbox relay iteration failed")
                result = None
            if result is not None and result.published >= relay.batch_size:
                continue
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=api_settings.relay_poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        relay_task: asyncio.Task[None] | None = None
        try:
            if redis_client is not None:
                await asyncio.to_thread(redis_client.ping)
            if relay is not None:
                relay_task = asyncio.create_task(run_relay(stop))
            yield
        finally:
            stop.set()
            if relay_task is not None:
                await relay_task
            if owns_service:
                task_service.close()
            if owns_redis and redis_client is not None:
                await asyncio.to_thread(redis_client.close)

    app = FastAPI(
        title="DevPilot Control API",
        summary="Human-in-the-loop control plane for DevPilot tasks",
        description=(
            "Phase 6 REST and WebSocket API. Authenticate in this page with a bearer token, "
            "then create tasks, inspect durable execution evidence, and issue revision-bound "
            "control commands. Chat messages never execute control operations."
        ),
        version="0.7.0",
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
    app.state.redis_client = redis_client
    app.state.outbox_relay = relay

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
