import pytest

from devpilot.cli import build_parser


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("cancel", ["--expected-revision", "1"]),
        ("rollback", ["--recovery-point-id", "recovery", "--expected-revision", "1"]),
        ("restore", ["--recovery-point-id", "recovery"]),
        ("resume", ["--expected-revision", "1"]),
    ],
)
def test_control_commands_accept_idempotency_key(command, extra):
    args = build_parser().parse_args(
        ["task", command, "--task-id", "task", *extra, "--idempotency-key", "stable-key"]
    )
    assert args.idempotency_key == "stable-key"
