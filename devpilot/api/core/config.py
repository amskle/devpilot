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

    @classmethod
    def from_env(cls) -> "ApiSettings":
        raw_tokens = os.environ.get("DEVPILOT_API_TOKENS")
        if raw_tokens:
            parsed: Any = json.loads(raw_tokens)
            if not isinstance(parsed, dict):
                raise ValueError("DEVPILOT_API_TOKENS must be a JSON object")
            tokens: dict[str, Principal] = {}
            for token, value in parsed.items():
                if isinstance(value, str):
                    tokens[str(token)] = Principal(value)
                elif isinstance(value, dict) and value.get("subject"):
                    tokens[str(token)] = Principal(
                        str(value["subject"]), bool(value.get("admin", False))
                    )
                else:
                    raise ValueError("each API token must map to a subject or principal object")
        else:
            tokens = {"devpilot-local": Principal("local-developer", True)}
        raw_origins = os.environ.get("DEVPILOT_API_CORS_ORIGINS", "")
        origins = tuple(item.strip() for item in raw_origins.split(",") if item.strip())
        return cls(tokens=tokens, cors_origins=origins)
