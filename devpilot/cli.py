from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from devpilot.service import TaskService


def _print_state(state: dict[str, Any]) -> None:
    print(json.dumps(state, ensure_ascii=False, indent=2))


def _service(args: argparse.Namespace) -> TaskService:
    return TaskService(
        data_dir=Path(args.data_dir) if args.data_dir else None,
        model=os.environ.get("DEVPILOT_MODEL", "gpt-5-mini"),
        base_url=os.environ.get("DEVPILOT_MODEL_BASE_URL"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devpilot")
    parser.add_argument("--data-dir", default=None)
    groups = parser.add_subparsers(dest="group", required=True)
    task = groups.add_parser("task")
    commands = task.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--repo", required=True)
    create.add_argument("--request", required=True)
    create.add_argument("--revision", default="HEAD")

    commands.add_parser("list")
    status = commands.add_parser("status")
    status.add_argument("--task-id", required=True)

    for name in ("approve", "reject"):
        decision = commands.add_parser(name)
        decision.add_argument("--task-id", required=True)
        decision.add_argument("--approval-id", required=True)
        decision.add_argument("--patch-hash", required=True)
        decision.add_argument("--base-revision", required=True)
        decision.add_argument("--expected-revision", type=int, required=True)
        decision.add_argument("--idempotency-key", default=None)

    cancel = commands.add_parser("cancel")
    cancel.add_argument("--task-id", required=True)
    cancel.add_argument("--expected-revision", type=int, required=True)

    rollback = commands.add_parser("rollback")
    rollback.add_argument("--task-id", required=True)
    rollback.add_argument("--recovery-point-id", required=True)
    rollback.add_argument("--expected-revision", type=int, required=True)

    restore = commands.add_parser("restore")
    restore.add_argument("--task-id", required=True)
    restore.add_argument("--recovery-point-id", required=True)

    resume = commands.add_parser("resume")
    resume.add_argument("--task-id", required=True)
    resume.add_argument("--expected-revision", type=int, required=True)

    admin = groups.add_parser("admin")
    admin_commands = admin.add_subparsers(dest="command", required=True)
    reconcile = admin_commands.add_parser("reconcile")
    reconcile.add_argument("--task-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    service = _service(args)
    try:
        if args.group == "admin":
            print(json.dumps({"reconciled": service.reconcile(args.task_id)}))
            return
        if args.command == "create":
            state = service.create_task(Path(args.repo), args.request, revision=args.revision)
        elif args.command == "list":
            print(json.dumps(service.control.list_tasks(), ensure_ascii=False, indent=2))
            return
        elif args.command == "status":
            state = service.get_state(args.task_id)
        elif args.command == "resume":
            state = service.resume(args.task_id, args.expected_revision)
        elif args.command in {"approve", "reject"}:
            key = args.idempotency_key or str(uuid.uuid4())
            if args.idempotency_key is None:
                print(f"idempotency_key={key}", file=sys.stderr)
            state = service.decide_approval(
                args.task_id,
                decision="APPROVE" if args.command == "approve" else "REJECT",
                approval_id=args.approval_id,
                patch_hash=args.patch_hash,
                base_revision=args.base_revision,
                expected_revision=args.expected_revision,
                idempotency_key=key,
            )
        elif args.command == "cancel":
            state = service.cancel(args.task_id, args.expected_revision)
        elif args.command == "rollback":
            state = service.rollback(args.task_id, args.recovery_point_id, args.expected_revision)
        elif args.command == "restore":
            state = service.restore(args.task_id, args.recovery_point_id)
        else:  # pragma: no cover
            raise AssertionError(args.command)
        _print_state(state)
    finally:
        service.close()


if __name__ == "__main__":
    main()
