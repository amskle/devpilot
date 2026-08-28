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


def test_api_refuses_public_bind_with_default_development_token(monkeypatch):
    monkeypatch.delenv("DEVPILOT_API_TOKENS", raising=False)
    with pytest.raises(SystemExit, match="DEVPILOT_API_TOKENS"):
        main(["api", "--host", "0.0.0.0"])
