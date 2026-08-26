from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.report import build_report
from shared_state.schema import TaskState, TaskStatus
from shared_state.store import MemoryStore
from skills.registry import run_skill


FIXABLE_ISSUES = {"mutable-default-argument", "bare-except"}


def _fix_mutable_default(content: str) -> tuple[str, dict | None]:
    pattern = re.compile(r"^(\s*)(def\s+\w+\([^)]*?)(\w+)(\s*=\s*\[\])(\s*\)\s*:.*)$", re.M)
    match = pattern.search(content)
    if not match:
        return content, None
    indent, prefix, param, default, rest = match.groups()
    new_def = f"{indent}{prefix}{param}{default.replace('= []', '=None').replace('=[]', '=None')}{rest}"
    guard = f"{indent}    if {param} is None:\n{indent}        {param} = []\n"
    old = match.group(0)
    new = new_def + "\n" + guard
    return content.replace(old, new, 1), {"old": old, "new": new}


def _fix_bare_except(content: str) -> tuple[str, dict | None]:
    pattern = re.compile(r"^(\s*)except\s*:(\s*)$", re.M)
    match = pattern.search(content)
    if not match:
        return content, None
    old = match.group(0)
    new = f"{match.group(1)}except Exception:{match.group(2)}"
    return content.replace(old, new, 1), {"old": old, "new": new}


def _patch_specs(task: TaskState) -> list[dict[str, Any]]:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for issue in task.issues:
        if issue["issue"] not in FIXABLE_ISSUES:
            continue
        location = issue.get("location", "")
        path_text = location.rpartition(":")[0] if location else ""
        if path_text and Path(path_text).exists():
            by_file.setdefault(path_text, []).append(issue)

    specs = []
    for path_text, issues in by_file.items():
        path = Path(path_text)
        original = path.read_text(encoding="utf-8", errors="ignore")
        content = original
        replacements = []
        for issue in issues:
            if issue["issue"] == "mutable-default-argument":
                content, replacement = _fix_mutable_default(content)
            else:
                content, replacement = _fix_bare_except(content)
            if replacement is not None:
                replacements.append(replacement)
        if replacements:
            specs.append(
                {
                    "file": str(path),
                    "issues": [i["issue"] for i in issues],
                    "replacements": replacements,
                    "new_content": content,
                }
            )
    return specs


def _generate_diff(path: Path, original: str, new_content: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )


def run_pipeline(repo_path: str, approval: str = "auto", output_dir: str | None = None) -> TaskState:
    repo = Path(repo_path).resolve()
    if not repo.exists():
        raise SystemExit(f"repo not found: {repo}")

    store = MemoryStore()
    task = TaskState(task_id=str(uuid.uuid4())[:8], repo_path=str(repo))
    store.put(task)

    task.event("Diagnosis Worker", "task_started", {"repo": str(repo)})
    context = run_skill("project-context", {"repo_path": str(repo)})
    task.context = context
    if context["status"] != "ok":
        task.status = TaskStatus.FAILED
        task.event("Diagnosis Worker", "context_failed", {"error": context.get("error")})
        return task

    project_type = context["data"]["project_type"]
    language = "auto" if project_type == "unknown" else project_type
    analysis = run_skill("code-analysis", {"repo_path": str(repo), "language": language})
    bugs = run_skill("bug-detection", {"repo_path": str(repo), "language": language})
    security = run_skill("security-scan", {"repo_path": str(repo)})
    task.issues = bugs["data"]["issues"] + security["data"]["issues"]
    task.event("Diagnosis Worker", "diagnosis_completed", {"issue_count": len(task.issues)})

    task.status = TaskStatus.PLANNING
    task.current_agent = "Planning Worker"
    fixable = [i for i in task.issues if i["issue"] in FIXABLE_ISSUES]
    report_only = [i for i in task.issues if i["issue"] not in FIXABLE_ISSUES]
    task.plan = {
        "summary": f"发现 {len(task.issues)} 项问题，{len(fixable)} 项可自动修复，{len(report_only)} 项仅报告",
        "patches": _patch_specs(task),
        "report_only": [i["issue"] for i in report_only],
    }
    task.event("Planning Worker", "plan_created", {"fixable": len(fixable), "report_only": len(report_only)})

    if task.plan["patches"]:
        task.status = TaskStatus.AWAITING_APPROVAL
        task.current_agent = "Manager"
        task.approval = {"mode": approval, "result": "approved" if approval == "auto" else "pending"}
        if approval != "auto":
            answer = input(f"批准 {len(task.plan['patches'])} 项自动修复？[y/N] ").strip().lower()
            task.approval["result"] = "approved" if answer == "y" else "rejected"
        if task.approval["result"] != "approved":
            task.status = TaskStatus.COMPLETED
            task.event("Manager", "approval_rejected", {})
            return task
        task.event("Manager", "approval_granted", {"mode": approval})

        task.status = TaskStatus.MODIFYING
        task.current_agent = "Modification Worker"
        backups: list[tuple[Path, str]] = []
        for spec in task.plan["patches"]:
            path = Path(spec["file"])
            original = path.read_text(encoding="utf-8", errors="ignore")
            backups.append((path, original))
            path.write_text(spec["new_content"], encoding="utf-8")
            task.diffs.append(
                {
                    "file": str(path),
                    "issue": "、".join(spec["issues"]),
                    "diff": _generate_diff(path, original, spec["new_content"]),
                }
            )
        task.event("Modification Worker", "patches_applied", {"count": len(backups)})

        task.status = TaskStatus.VERIFYING
        task.current_agent = "Verification Worker"
        task.verification = run_skill(
            "test-execution",
            {"command": "python -m unittest discover -s . -q", "cwd": str(repo), "timeout": 120},
        )
        passed = task.verification.get("data", {}).get("passed", False)
        if not passed:
            for path, original in backups:
                path.write_text(original, encoding="utf-8")
            task.status = TaskStatus.FAILED
            task.event("Verification Worker", "verification_failed_rolled_back", {"files": [str(p) for p, _ in backups]})
        else:
            task.status = TaskStatus.COMPLETED
            task.event("Verification Worker", "verification_passed", {})
    else:
        task.status = TaskStatus.COMPLETED
        task.event("Verification Worker", "no_changes_to_verify", {})

    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        task.current_agent = "Review Worker"
        notes = (
            f"Problem: {'、'.join(i['issue'] for i in task.issues) or '无'}\n"
            f"Solution: 自动修复 + 测试验证\n"
            f"Rule: 检测到可复现缺陷时，先生成 diff，审批后应用并执行测试"
        )
        task.knowledge = run_skill(
            "knowledge-extract",
            {"notes": notes, "tags": [i["issue"] for i in task.issues]},
        )
        task.event("Review Worker", "knowledge_extracted", {} if task.knowledge["status"] == "ok" else {"error": task.knowledge.get("error")})

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(build_report(task), encoding="utf-8")
        (out / "state.json").write_text(task.to_json(), encoding="utf-8")

    store.put(task)
    return task


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DevPilot Infra local pipeline")
    parser.add_argument("--repo", required=True, help="path to target repository")
    parser.add_argument("--approval", default="auto", choices=["auto", "confirm"])
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    task = run_pipeline(args.repo, args.approval, args.output_dir)
    print(build_report(task))
    if task.status == TaskStatus.FAILED:
        sys.exit(1)


# The canonical implementation is the LangGraph compatibility facade.  The
# definitions above remain temporarily for source-level import compatibility
# with the original competition submission and are not an executable backend.
from runtime.compat_pipeline import main, run_pipeline  # noqa: E402,F811


if __name__ == "__main__":
    main()
