from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"
MAX_EVENT_STRING_LENGTH = 16_000

_SECRET_NAMES = {
    "apikey",
    "authorization",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "env",
    "environment",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
    "accesstoken",
}
_SECRET_SUFFIXES = ("apikey", "password", "privatekey", "secret", "token")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[-_ ]?key|password|private[-_ ]?key|secret|token)\s*[:=]\s*[^\s,;]+"
)


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _is_secret_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    return normalized in _SECRET_NAMES or normalized.endswith(_SECRET_SUFFIXES)


def sanitize_event_value(value: Any) -> Any:
    """Return JSON-safe audit data with credentials removed and large text bounded."""

    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_secret_key(key) else sanitize_event_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_event_value(item) for item in value]
    if isinstance(value, str):
        sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}=[REDACTED]", sanitized
        )
        if len(sanitized) > MAX_EVENT_STRING_LENGTH:
            return sanitized[:MAX_EVENT_STRING_LENGTH] + "...[TRUNCATED]"
        return sanitized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)
