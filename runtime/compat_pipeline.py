from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any

from devpilot.domain.models import TaskStatus as NewTaskStatus
from devpilot.service import TaskService
from devpilot.testing.scenarios import legacy_scenario
from runtime.report import build_report
from shared_state.schema import TaskState, TaskStatus


LEGACY_STATUS_MAP = {
    NewTaskStatus.CREATED.value: TaskStatus.ANALYZING,
    NewTaskStatus.WAITING_RISK_APPROVAL.value: TaskStatus.AWAITING_APPROVAL,
    NewTaskStatus.WAITING_HUMAN_INTERVENTION.value: TaskStatus.FAILED,
    NewTaskStatus.CANCELLING.value: TaskStatus.FAILED,
    NewTaskStatus.CANCELLED.value: TaskStatus.FAILED,
    NewTaskStatus.COMPLETED.value: TaskStatus.COMPLETED,
    NewTaskStatus.COMPLETED_NO_CHANGES.value: TaskStatus.COMPLETED,
    NewTaskStatus.FAILED.value: TaskStatus.FAILED,
    NewTaskStatus.POLICY_REJECTED.value: TaskStatus.FAILED,
}


def legacy_status(state: dict[str, Any]) -> TaskStatus:
    if state["status"] != NewTaskStatus.RUNNING.value:
        return LEGACY_STATUS_MAP[state["status"]]
    node = state["current_node"]
    if node in {"workspace_setup", "baseline_context"}:
        return TaskStatus.ANALYZING
    if node in {"planning", "diagnosis"}:
        return TaskStatus.PLANNING
    if node in {"run_verification", "parse_verification", "evaluate_progress"}:
        return TaskStatus.VERIFYING
    return TaskStatus.MODIFYING


def _ensure_git_source(source: Path, target: Path) -> Path:
    probe = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    if probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == source.resolve():
        clean = subprocess.run(
            ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, check=False,
        )
        if not clean.stdout.strip():
            return source
    imported = target / "legacy-source"
    imported.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, imported, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    for command in (
        ["git", "init"],
        ["git", "config", "user.name", "DevPilot"],
        ["git", "config", "user.email", "devpilot@local"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "legacy import baseline"],
    ):
        proc = subprocess.run(command, cwd=imported, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout)
    return imported


def _project_legacy(service: TaskService, state: dict[str, Any], original_repo: Path) -> TaskState:
    task = TaskState(task_id=state["task_id"], repo_path=str(original_repo), status=legacy_status(state))
    task.current_agent = {
        "planning": "Planning Worker", "diagnosis": "Diagnosis Worker",
        "patch_generation": "Modification Worker", "review": "Review Worker",
    }.get(state["current_node"], "Manager")
    if state["baseline_context_ref"]:
        task.context = {
            "status": "ok",
            "data": json.loads(service.artifacts.read_text(state["task_id"], state["run_id"], state["baseline_context_ref"])),
        }
    task.issues = list((state.get("diagnosis") or {}).get("issues", []))
    if state["active_plan_ref"]:
        plan = json.loads(service.artifacts.read_text(state["task_id"], state["run_id"], state["active_plan_ref"]))
        task.plan = {
            "summary": plan.get("summary", ""),
            "patches": [state["patch_proposal"]] if state["patch_proposal"] else [],
            "report_only": [
                issue["issue"] for issue in task.issues
                if issue["issue"] not in {"mutable-default-argument", "bare-except"}
            ],
        }
    if state["patch_proposal"]:
        proposal = state["patch_proposal"]
        diff = service.artifacts.read_text(state["task_id"], state["run_id"], proposal["patch_ref"])
        task.diffs = [{"file": name, "issue": proposal["summary"], "diff": diff} for name in proposal["changed_files"]]
    if state["verification"]:
        task.verification = {
            "status": "ok",
            "data": {key: value for key, value in state["verification"].items() if key != "report_ref"},
        }
    if state["review"]:
        task.knowledge = {
            "status": "ok",
            "data": {
                "problem_pattern": ", ".join(issue["issue"] for issue in task.issues) or "none",
                "solution_pattern": state["review"]["summary"],
                "reusable_rule": "; ".join(state["review"].get("lessons", [])),
            },
        }
    if state["pending_approval"]:
        task.approval = {"mode": "confirm", "result": "pending", **state["pending_approval"]}
    task.history = [
        {"event": event["event_type"], "agent": event["payload"].get("node", "LangGraph"), "detail": event["payload"]}
        for event in service.control.events(state["task_id"])
    ]
    return task


def run_pipeline(repo_path: str, approval: str = "auto", output_dir: str | None = None) -> TaskState:
    warnings.warn("runtime.run_pipeline is deprecated; use devpilot TaskService or CLI", DeprecationWarning, stacklevel=2)
    source = Path(repo_path).resolve()
    if not source.exists():
        raise SystemExit(f"repo not found: {source}")
    temporary = tempfile.TemporaryDirectory()
    root = Path(output_dir).resolve() / "_devpilot" if output_dir else Path(temporary.name) / "data"
    imported = _ensure_git_source(source, root.parent)
    service = TaskService(
        data_dir=root,
        gateway=legacy_scenario(imported),
        verification_command="python -m unittest discover -s . -q",
    )
    try:
        state = service.create_task(imported, "Diagnose and safely fix supported issues")
        if state["status"] == NewTaskStatus.WAITING_RISK_APPROVAL.value and approval == "confirm":
            pending = state["pending_approval"] or {}
            answer = input("Approve proposed patch? [y/N] ").strip().lower()
            state = service.decide_approval(
                state["task_id"], decision="APPROVE" if answer == "y" else "REJECT",
                approval_id=pending["approval_id"], patch_hash=pending["patch_hash"],
                base_revision=pending["base_revision"], expected_revision=state["state_revision"],
            )
        task = _project_legacy(service, state, source)
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.md").write_text(build_report(task), encoding="utf-8")
            (out / "state.json").write_text(task.to_json(), encoding="utf-8")
        return task
    finally:
        service.close()
        temporary.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deprecated DevPilot compatibility pipeline")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--approval", default="auto", choices=["auto", "confirm"])
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    task = run_pipeline(args.repo, args.approval, args.output_dir)
    print(build_report(task))
    if task.status == TaskStatus.FAILED:
        sys.exit(1)
