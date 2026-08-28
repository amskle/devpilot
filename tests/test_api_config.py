import logging

import pytest

from devpilot.api import create_app
from devpilot.api.core.config import ApiSettings


def test_non_development_environment_requires_explicit_api_tokens(monkeypatch):
    monkeypatch.setenv("DEVPILOT_ENV", "production")
    monkeypatch.delenv("DEVPILOT_API_TOKENS", raising=False)

    with pytest.raises(ValueError, match="DEVPILOT_API_TOKENS"):
        ApiSettings.from_env()


def test_development_default_token_is_marked_and_warned(monkeypatch, caplog):
    monkeypatch.setenv("DEVPILOT_ENV", "development")
    monkeypatch.delenv("DEVPILOT_API_TOKENS", raising=False)
    settings = ApiSettings.from_env()

    with caplog.at_level(logging.WARNING, logger="devpilot.api.main"):
        create_app(service=object(), settings=settings)  # type: ignore[arg-type]

    assert settings.uses_default_token is True
    assert settings.tokens["devpilot-local"].is_admin is True
    assert "SECURITY WARNING" in caplog.text


def test_api_token_mapping_must_not_be_empty(monkeypatch):
    monkeypatch.setenv("DEVPILOT_ENV", "development")
    monkeypatch.setenv("DEVPILOT_API_TOKENS", "{}")

    with pytest.raises(ValueError, match="at least one token"):
        ApiSettings.from_env()
