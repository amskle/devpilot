import pytest

from devpilot.cli import build_parser, main


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("cancel", ["--expected-revision", "1"]),
        ("rollback", ["--recovery-point-id", "recovery", "--expected-revision", "1"]),
        ("restore", ["--recovery-point-id", "recovery"]),
        ("resume", ["--expected-revision", "1"]),
        ("replan", ["--expected-revision", "1", "--reason", "revise assumptions"]),
    ],
)
def test_control_commands_accept_idempotency_key(command, extra):
    args = build_parser().parse_args(
        ["task", command, "--task-id", "task", *extra, "--idempotency-key", "stable-key"]
    )
    assert args.idempotency_key == "stable-key"


def test_plan_history_command_accepts_task_id():
    args = build_parser().parse_args(["task", "plan", "--task-id", "task"])
    assert args.command == "plan"
    assert args.task_id == "task"


def test_task_create_accepts_explicit_budget_limits():
    args = build_parser().parse_args(
        [
            "task",
            "create",
            "--repo",
            "repo",
            "--request",
            "inspect",
            "--max-total-tokens",
            "250000",
            "--max-llm-calls",
            "30",
        ]
    )

    assert args.max_total_tokens == 250_000
    assert args.max_llm_calls == 30


def test_task_create_passes_only_limit_overrides_to_runtime(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            pass

        def create_task(self, repo, request, **kwargs):
            captured.update({"repo": repo, "request": request, **kwargs})
            return {"status": "COMPLETED_NO_CHANGES"}

        def close(self):
            pass

    monkeypatch.setattr("devpilot.cli.TaskService", FakeService)

    main(
        [
            "task",
            "create",
            "--repo",
            str(tmp_path),
            "--request",
            "inspect",
            "--max-total-tokens",
            "250000",
            "--max-tool-calls",
            "80",
        ]
    )

    assert captured["budget"].max_total_tokens == 250_000
    assert captured["budget"].max_tool_calls == 80
    assert captured["budget"].prompt_tokens_used == 0
    assert "COMPLETED_NO_CHANGES" in capsys.readouterr().out


def test_api_refuses_public_bind_with_default_development_token(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVPILOT_API_TOKENS", raising=False)
    with pytest.raises(SystemExit, match="DEVPILOT_API_TOKENS"):
        main(["api", "--host", "0.0.0.0"])


def test_cli_accepts_an_explicit_env_file():
    args = build_parser().parse_args(
        ["--env-file", "settings.env", "task", "list"]
    )
    assert args.env_file == "settings.env"


def test_cli_rejects_a_missing_explicit_env_file(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "--env-file",
                str(tmp_path / "missing.env"),
                "task",
                "list",
            ]
        )


def test_cli_loads_model_settings_from_default_env_file(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / ".env").write_text(
        "DEVPILOT_MODEL_API_KEY=test-key\n"
        "DEVPILOT_MODEL_BASE_URL=https://example.invalid/v1\n"
        "DEVPILOT_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "DEVPILOT_MODEL_API_KEY",
        "DEVPILOT_MODEL_BASE_URL",
        "DEVPILOT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    captured = {}

    class FakeControl:
        @staticmethod
        def list_tasks():
            return []

    class FakeService:
        control = FakeControl()

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr("devpilot.cli.TaskService", FakeService)

    main(["task", "list"])

    assert captured["model"] == "file-model"
    assert captured["base_url"] == "https://example.invalid/v1"
    assert capsys.readouterr().out.strip() == "[]"


def test_phase7_replay_commands_parse_targets():
    events = build_parser().parse_args(
        [
            "replay",
            "events",
            "--task-id",
            "task",
            "--run-id",
            "run",
            "--through-sequence",
            "4",
        ]
    )
    state = build_parser().parse_args(
        ["replay", "state", "--task-id", "task", "--state-revision", "2"]
    )
    fork = build_parser().parse_args(
        [
            "replay",
            "fork",
            "--task-id",
            "task",
            "--recovery-point-id",
            "recovery",
        ]
    )

    assert (events.run_id, events.through_sequence) == ("run", 4)
    assert state.state_revision == 2
    assert fork.recovery_point_id == "recovery"


def test_task_delete_requires_explicit_confirmation_flag():
    parsed = build_parser().parse_args(
        [
            "task",
            "delete",
            "--task-id",
            "task_1",
            "--task-id",
            "task_2",
            "--yes",
        ]
    )

    assert parsed.command == "delete"
    assert parsed.task_id == ["task_1", "task_2"]
    assert parsed.yes is True


def test_phase7_evaluation_commands_parse_comparison():
    run = build_parser().parse_args(
        [
            "eval",
            "run",
            "--dataset",
            "dataset.yaml",
            "--model",
            "candidate-model",
            "--prompt-version",
            "v2",
            "--prompt-overrides",
            "prompts.yaml",
        ]
    )
    compare = build_parser().parse_args(
        ["eval", "compare", "--baseline", "eval-a", "--candidate", "eval-b"]
    )

    assert (run.model, run.prompt_version, run.prompt_overrides) == (
        "candidate-model",
        "v2",
        "prompts.yaml",
    )
    assert (compare.baseline, compare.candidate) == ("eval-a", "eval-b")


def test_alerts_list_command_parses_scope():
    unscoped = build_parser().parse_args(["alerts", "list"])
    scoped = build_parser().parse_args(
        ["alerts", "list", "--task-id", "task", "--limit", "10"]
    )

    assert (unscoped.group, unscoped.command) == ("alerts", "list")
    assert (unscoped.task_id, unscoped.limit) == (None, None)
    assert (scoped.task_id, scoped.limit) == ("task", 10)


def test_alerts_main_prints_history(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def alert_history(self, task_id, *, limit=None):
            captured["scope"] = (task_id, limit)
            return [{"alert_id": "alert_x", "rule": "APPROVAL_PENDING"}]

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr("devpilot.cli.TaskService", FakeService)

    main(["alerts", "list", "--task-id", "task"])

    assert captured["scope"] == ("task", None)
    assert captured["closed"] is True
    assert "APPROVAL_PENDING" in capsys.readouterr().out
