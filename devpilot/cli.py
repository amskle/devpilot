from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

from devpilot.domain.replay import EvaluationDataset
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
    api = groups.add_parser("api", help="run the Phase 6 FastAPI control plane")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true")
    api.add_argument("--workers", type=int, default=1)
    task = groups.add_parser("task")
    commands = task.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--repo", required=True)
    create.add_argument("--request", required=True)
    create.add_argument("--revision", default="HEAD")

    commands.add_parser("list")
    status = commands.add_parser("status")
    status.add_argument("--task-id", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--task-id", required=True)

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
    cancel.add_argument("--idempotency-key", default=None)

    rollback = commands.add_parser("rollback")
    rollback.add_argument("--task-id", required=True)
    rollback.add_argument("--recovery-point-id", required=True)
    rollback.add_argument("--expected-revision", type=int, required=True)
    rollback.add_argument("--idempotency-key", default=None)

    restore = commands.add_parser("restore")
    restore.add_argument("--task-id", required=True)
    restore.add_argument("--recovery-point-id", required=True)
    restore.add_argument("--idempotency-key", default=None)

    resume = commands.add_parser("resume")
    resume.add_argument("--task-id", required=True)
    resume.add_argument("--expected-revision", type=int, required=True)
    resume.add_argument("--idempotency-key", default=None)

    replan = commands.add_parser("replan")
    replan.add_argument("--task-id", required=True)
    replan.add_argument("--expected-revision", type=int, required=True)
    replan.add_argument("--reason", required=True)
    replan.add_argument("--idempotency-key", default=None)

    admin = groups.add_parser("admin")
    admin_commands = admin.add_subparsers(dest="command", required=True)
    reconcile = admin_commands.add_parser("reconcile")
    reconcile.add_argument("--task-id", required=True)

    replay = groups.add_parser("replay", help="replay durable events or state")
    replay_commands = replay.add_subparsers(dest="command", required=True)
    replay_events = replay_commands.add_parser("events")
    replay_events.add_argument("--task-id", required=True)
    replay_events.add_argument("--run-id", default=None)
    replay_events.add_argument("--through-sequence", type=int, default=None)
    replay_state = replay_commands.add_parser("state")
    replay_state.add_argument("--task-id", required=True)
    replay_state.add_argument("--run-id", default=None)
    replay_state.add_argument("--state-revision", type=int, default=None)
    replay_history = replay_commands.add_parser("history")
    replay_history.add_argument("--task-id", required=True)
    replay_fork = replay_commands.add_parser("fork")
    replay_fork.add_argument("--task-id", required=True)
    replay_fork.add_argument("--recovery-point-id", required=True)
    replay_fork.add_argument("--model", default=None)

    evaluation = groups.add_parser("eval", help="run and compare evaluations")
    evaluation_commands = evaluation.add_subparsers(dest="command", required=True)
    evaluation_run = evaluation_commands.add_parser("run")
    evaluation_run.add_argument("--dataset", required=True)
    evaluation_run.add_argument("--model", default=None)
    evaluation_run.add_argument("--prompt-version", default="default")
    evaluation_run.add_argument("--prompt-overrides", default=None)
    evaluation_show = evaluation_commands.add_parser("show")
    evaluation_show.add_argument("--evaluation-id", required=True)
    evaluation_commands.add_parser("list")
    evaluation_compare = evaluation_commands.add_parser("compare")
    evaluation_compare.add_argument("--baseline", required=True)
    evaluation_compare.add_argument("--candidate", required=True)
    return parser


def _load_evaluation_dataset(path: Path) -> EvaluationDataset:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(raw)
    else:
        value = yaml.safe_load(raw)
    return EvaluationDataset.model_validate(value)


def _load_mapping(path: Path) -> dict[str, str]:
    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError("prompt overrides must be a string-to-string object")
    return value


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.group == "api":
        if args.workers < 1:
            raise SystemExit("--workers must be positive")
        if args.reload and args.workers != 1:
            raise SystemExit("--reload and --workers cannot be used together")
        if args.workers > 1 and not os.environ.get("DEVPILOT_REDIS_URL"):
            raise SystemExit(
                "DEVPILOT_REDIS_URL must be configured before using multiple API workers"
            )
        if (
            args.host not in {"127.0.0.1", "localhost", "::1"}
            and not os.environ.get("DEVPILOT_API_TOKENS")
        ):
            raise SystemExit(
                "DEVPILOT_API_TOKENS must be configured before binding the API to a non-loopback host"
            )
        if args.data_dir:
            os.environ["DEVPILOT_DATA_DIR"] = str(Path(args.data_dir).resolve())
        os.environ["DEVPILOT_API_WORKERS"] = str(args.workers)
        import uvicorn

        uvicorn.run(
            "devpilot.api.main:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=args.workers,
        )
        return
    service = _service(args)
    try:
        if args.group == "admin":
            print(json.dumps({"reconciled": service.reconcile(args.task_id)}))
            return
        if args.group == "replay":
            if args.command == "events":
                value = service.replay_events(
                    args.task_id,
                    args.run_id,
                    through_sequence=args.through_sequence,
                )
            elif args.command == "state":
                value = service.replay_state(
                    args.task_id,
                    args.run_id,
                    state_revision=args.state_revision,
                )
            elif args.command == "history":
                value = service.replay_history(args.task_id)
            else:
                value = service.fork_recovery_point(
                    args.task_id,
                    args.recovery_point_id,
                    model=args.model,
                )
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return
        if args.group == "eval":
            if args.command == "run":
                dataset = _load_evaluation_dataset(Path(args.dataset))
                value = service.run_evaluation(
                    dataset,
                    model=args.model,
                    prompt_version=args.prompt_version,
                    prompt_overrides=(
                        _load_mapping(Path(args.prompt_overrides))
                        if args.prompt_overrides
                        else None
                    ),
                )
            elif args.command == "show":
                value = service.evaluation_report(args.evaluation_id)
            elif args.command == "list":
                value = service.evaluation_history()
            else:
                value = service.compare_evaluations(
                    args.baseline,
                    args.candidate,
                )
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return
        if args.command == "create":
            state = service.create_task(Path(args.repo), args.request, revision=args.revision)
        elif args.command == "list":
            print(json.dumps(service.control.list_tasks(), ensure_ascii=False, indent=2))
            return
        elif args.command == "status":
            state = service.get_state(args.task_id)
        elif args.command == "plan":
            print(json.dumps(service.plan_history(args.task_id), ensure_ascii=False, indent=2))
            return
        elif args.command == "resume":
            key = args.idempotency_key or str(uuid.uuid4())
            if args.idempotency_key is None:
                print(f"idempotency_key={key}", file=sys.stderr)
            state = service.resume(args.task_id, args.expected_revision, idempotency_key=key)
        elif args.command == "replan":
            key = args.idempotency_key or str(uuid.uuid4())
            if args.idempotency_key is None:
                print(f"idempotency_key={key}", file=sys.stderr)
            state = service.replan(
                args.task_id,
                args.expected_revision,
                reason=args.reason,
                idempotency_key=key,
            )
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
            key = args.idempotency_key or str(uuid.uuid4())
            if args.idempotency_key is None:
                print(f"idempotency_key={key}", file=sys.stderr)
            state = service.cancel(args.task_id, args.expected_revision, idempotency_key=key)
        elif args.command == "rollback":
            key = args.idempotency_key or str(uuid.uuid4())
            if args.idempotency_key is None:
                print(f"idempotency_key={key}", file=sys.stderr)
            state = service.rollback(
                args.task_id,
                args.recovery_point_id,
                args.expected_revision,
                idempotency_key=key,
            )
        elif args.command == "restore":
            key = args.idempotency_key or str(uuid.uuid4())
            if args.idempotency_key is None:
                print(f"idempotency_key={key}", file=sys.stderr)
            state = service.restore(args.task_id, args.recovery_point_id, idempotency_key=key)
        else:  # pragma: no cover
            raise AssertionError(args.command)
        _print_state(state)
    finally:
        service.close()


if __name__ == "__main__":
    main()
