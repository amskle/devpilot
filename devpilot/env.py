from __future__ import annotations

import os
import re
from pathlib import Path


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOUBLE_QUOTED_ESCAPE = re.compile(r"\\([\\\"nrt])")
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def _dotenv_value(raw: str, *, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise ValueError(f"unterminated quoted value on .env line {line_number}")
        unquoted = value[1:-1]
        if quote == '"':
            return _DOUBLE_QUOTED_ESCAPE.sub(
                lambda match: _ESCAPES[match.group(1)], unquoted
            )
        return unquoted
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def load_devpilot_env(path: Path | str = ".env", *, required: bool = False) -> bool:
    """Load DEVPILOT_* settings without overriding the process environment."""

    env_path = Path(path).expanduser()
    if not env_path.is_file():
        if required:
            raise FileNotFoundError(f"environment file not found: {env_path}")
        return False

    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            if line.startswith("DEVPILOT_"):
                raise ValueError(f"invalid .env assignment on line {line_number}")
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.fullmatch(key):
            raise ValueError(f"invalid .env key on line {line_number}: {key!r}")
        if not key.startswith("DEVPILOT_"):
            continue
        os.environ.setdefault(
            key,
            _dotenv_value(raw_value, line_number=line_number),
        )
    return True
