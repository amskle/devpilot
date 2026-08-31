import os

import pytest

from devpilot.env import load_devpilot_env


def test_load_devpilot_env_supports_quotes_comments_and_scope(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVPILOT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("DEVPILOT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("DEVPILOT_MODEL", raising=False)
    monkeypatch.delenv("UNRELATED_SETTING", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\ufeff# local model\n"
        "DEVPILOT_MODEL_API_KEY='secret-value'\n"
        'DEVPILOT_MODEL_BASE_URL="https://example.invalid/v1"\n'
        "DEVPILOT_MODEL=example-model  # selected model\n"
        "UNRELATED_SETTING=ignored\n",
        encoding="utf-8",
    )

    assert load_devpilot_env(env_file) is True
    assert os.environ["DEVPILOT_MODEL_API_KEY"] == "secret-value"
    assert os.environ["DEVPILOT_MODEL_BASE_URL"] == "https://example.invalid/v1"
    assert os.environ["DEVPILOT_MODEL"] == "example-model"
    assert "UNRELATED_SETTING" not in os.environ


def test_load_devpilot_env_preserves_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVPILOT_MODEL", "process-model")
    env_file = tmp_path / ".env"
    env_file.write_text("DEVPILOT_MODEL=file-model\n", encoding="utf-8")

    load_devpilot_env(env_file)

    assert os.environ["DEVPILOT_MODEL"] == "process-model"


def test_load_devpilot_env_handles_missing_and_invalid_files(tmp_path):
    missing = tmp_path / "missing.env"
    assert load_devpilot_env(missing) is False
    with pytest.raises(FileNotFoundError, match="environment file not found"):
        load_devpilot_env(missing, required=True)

    invalid = tmp_path / ".env"
    invalid.write_text('DEVPILOT_MODEL="unterminated\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unterminated quoted value"):
        load_devpilot_env(invalid)
