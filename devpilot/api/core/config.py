from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


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
    uses_default_token: bool = False

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
                    raise ValueError("API tokens must not be empty")
                if isinstance(value, str):
                    if not value.strip():
                        raise ValueError("API token subjects must not be empty")
                    tokens[str(token)] = Principal(value)
                elif isinstance(value, dict) and value.get("subject"):
                    if not str(value["subject"]).strip():
                        raise ValueError("API token subjects must not be empty")
                    tokens[str(token)] = Principal(
                        str(value["subject"]), bool(value.get("admin", False))
                    )
                else:
                    raise ValueError("each API token must map to a subject or principal object")
            if not tokens:
                raise ValueError("DEVPILOT_API_TOKENS must contain at least one token")
            uses_default_token = False
        else:
            if environment != "development":
                raise ValueError(
                    "DEVPILOT_API_TOKENS must be configured when "
                    f"DEVPILOT_ENV={environment}"
                )
            tokens = {"devpilot-local": Principal("local-developer", True)}
            uses_default_token = True
        raw_origins = os.environ.get("DEVPILOT_API_CORS_ORIGINS", "")
        origins = tuple(item.strip() for item in raw_origins.split(",") if item.strip())
        return cls(
            tokens=tokens,
            cors_origins=origins,
            environment=environment,
            uses_default_token=uses_default_token,
        )
