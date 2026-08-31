import json
import logging

import pytest

from devpilot.api import create_app
from devpilot.api.core.config import ApiSettings, Principal


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


def test_repository_roots_are_parsed_as_a_json_path_list(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVPILOT_ENV", "development")
    monkeypatch.setenv("DEVPILOT_API_TOKENS", '{"token":"alice"}')
    monkeypatch.setenv(
        "DEVPILOT_API_REPOSITORY_ROOTS",
        f'["{str(tmp_path).replace(chr(92), chr(92) * 2)}"]',
    )

    settings = ApiSettings.from_env()

    assert settings.repository_roots == (tmp_path.resolve(),)


def test_repository_roots_reject_relative_or_missing_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVPILOT_ENV", "development")
    monkeypatch.setenv("DEVPILOT_API_TOKENS", '{"token":"alice"}')
    monkeypatch.setenv("DEVPILOT_API_REPOSITORY_ROOTS", '["relative"]')
    with pytest.raises(ValueError, match="absolute"):
        ApiSettings.from_env()

    monkeypatch.setenv(
        "DEVPILOT_API_REPOSITORY_ROOTS",
        json.dumps([str(tmp_path / "missing")]),
    )
    with pytest.raises(ValueError, match="existing directories"):
        ApiSettings.from_env()


def test_production_rejects_short_static_tokens(monkeypatch):
    monkeypatch.setenv("DEVPILOT_ENV", "production")
    monkeypatch.setenv("DEVPILOT_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("DEVPILOT_API_TOKENS", '{"short":"alice"}')

    with pytest.raises(ValueError, match="at least 32"):
        ApiSettings.from_env()


def test_api_owned_task_service_uses_the_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVPILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEVPILOT_MODEL", "configured-model")
    monkeypatch.setenv(
        "DEVPILOT_MODEL_BASE_URL", "https://models.example.test/compatible/v1"
    )
    settings = ApiSettings(tokens={"token": Principal("test-user")})

    app = create_app(settings=settings)
    service = app.state.task_service
    try:
        assert service.model_name == "configured-model"
        assert service.gateway.options == {
            "model": "configured-model",
            "base_url": "https://models.example.test/compatible/v1",
            "api_key": None,
        }
    finally:
        service.close()
