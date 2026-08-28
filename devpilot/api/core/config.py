from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class Principal:
    subject: str
    is_admin: bool = False


@dataclass(frozen=True)
class ApiSettings:
    tokens: dict[str, Principal]
    ticket_ttl_seconds: int = 30
    cors_origins: tuple[str, ...] = ()
    environment: str = "development"
    redis_url: str | None = None
    redis_key_prefix: str = "devpilot"
    worker_count: int = 1
    relay_poll_interval_seconds: float = 0.25
    uses_default_token: bool = False

    def __post_init__(self) -> None:
        if not self.tokens:
            raise ValueError("at least one API token must be configured")
        if self.ticket_ttl_seconds < 1:
            raise ValueError("ticket_ttl_seconds must be positive")
        if self.worker_count < 1:
            raise ValueError("worker_count must be positive")
        if self.relay_poll_interval_seconds <= 0:
            raise ValueError("relay_poll_interval_seconds must be positive")
        if not self.redis_key_prefix.strip(": "):
            raise ValueError("redis_key_prefix must not be empty")
        if self.redis_url is not None:
            parsed = urlparse(self.redis_url)
            if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
                raise ValueError(
                    "DEVPILOT_REDIS_URL must be a redis:// or rediss:// URL"
                )
        if self.worker_count > 1 and self.redis_url is None:
            raise ValueError("multi-worker API deployment requires DEVPILOT_REDIS_URL")
        if self.environment != "development" and self.redis_url is None:
            raise ValueError(
                "non-development API deployment requires DEVPILOT_REDIS_URL"
            )

    @classmethod
    def from_env(cls) -> "ApiSettings":
        environment = os.environ.get("DEVPILOT_ENV", "development").strip().lower()
        if not environment:
            raise ValueError("DEVPILOT_ENV must not be empty")
        raw_tokens = os.environ.get("DEVPILOT_API_TOKENS")
        if raw_tokens:
            parsed: Any = json.loads(raw_tokens)
            if not isinstance(parsed, dict):
                raise ValueError("DEVPILOT_API_TOKENS must be a JSON object")
            tokens: dict[str, Principal] = {}
            for token, value in parsed.items():
                if not str(token).strip():
                    raise ValueError("API token values must not be empty")
                if isinstance(value, str):
                    if not value.strip():
                        raise ValueError("API token subjects must not be empty")
                    tokens[str(token)] = Principal(value)
                elif isinstance(value, dict) and value.get("subject"):
                    tokens[str(token)] = Principal(
                        str(value["subject"]), bool(value.get("admin", False))
                    )
                else:
                    raise ValueError(
                        "each API token must map to a subject or principal object"
                    )
            uses_default_token = False
        else:
            if environment != "development":
                raise ValueError(
                    "DEVPILOT_API_TOKENS is required outside development"
                )
            tokens = {"devpilot-local": Principal("local-developer", True)}
            uses_default_token = True
        raw_origins = os.environ.get("DEVPILOT_API_CORS_ORIGINS", "")
        origins = tuple(item.strip() for item in raw_origins.split(",") if item.strip())
        redis_url = os.environ.get("DEVPILOT_REDIS_URL")
        worker_count = int(os.environ.get("DEVPILOT_API_WORKERS", "1"))
        ticket_ttl = int(os.environ.get("DEVPILOT_EVENT_TICKET_TTL_SECONDS", "30"))
        relay_interval = float(os.environ.get("DEVPILOT_RELAY_POLL_SECONDS", "0.25"))
        return cls(
            tokens=tokens,
            ticket_ttl_seconds=ticket_ttl,
            cors_origins=origins,
            environment=environment,
            redis_url=redis_url.strip() if redis_url else None,
            redis_key_prefix=os.environ.get("DEVPILOT_REDIS_KEY_PREFIX", "devpilot"),
            worker_count=worker_count,
            relay_poll_interval_seconds=relay_interval,
            uses_default_token=uses_default_token,
        )
