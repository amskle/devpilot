from __future__ import annotations

import re
from pathlib import Path

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from skills.registry import run_skill


FIXABLE_ISSUES = {"mutable-default-argument", "bare-except"}


def _operations(repo: Path, issues: list[dict]) -> list[dict]:
    operations_by_file: dict[str, dict] = {}
    for issue in issues:
        if issue["issue"] not in FIXABLE_ISSUES:
            continue
        path = Path(issue.get("location", "").rpartition(":")[0])
        if not path.is_file():
            continue
        relative = path.resolve().relative_to(repo.resolve()).as_posix()
        content = path.read_text(encoding="utf-8", errors="ignore")
        if issue["issue"] == "mutable-default-argument":
            match = re.search(r"^(\s*)def\s+(\w+)\(([^)]*?)(\w+)\s*=\s*\[\](.*?)\)\s*:", content, re.M)
            if not match:
                continue
            indent, name, before, param, after = match.groups()
            old = match.group(0)
            new = f"{indent}def {name}({before}{param}=None{after}):\n{indent}    if {param} is None:\n{indent}        {param} = []"
        else:
            match = re.search(r"^(\s*)except\s*:\s*$", content, re.M)
            if not match:
                continue
            old = match.group(0)
            new = f"{match.group(1)}except Exception:"
        operation = operations_by_file.setdefault(
            relative,
            {"target_file": relative, "issues": [], "replacements": []},
        )
        operation["issues"].append(issue["issue"])
        operation["replacements"].append(
            {"old": old, "new": new, "occurrence": 1}
        )
    return list(operations_by_file.values())


def legacy_scenario(repo: Path) -> ScriptedFakeModelGateway:
    context = run_skill("project-context", {"repo_path": str(repo)})["data"]
    language = "auto" if context["project_type"] == "unknown" else context["project_type"]
    bugs = run_skill("bug-detection", {"repo_path": str(repo), "language": language})["data"]["issues"]
    security = run_skill("security-scan", {"repo_path": str(repo)})["data"]["issues"]
    issues = bugs + security
    operations = _operations(repo, issues)
    outcome = "ISSUE_FOUND" if operations else "NO_ACTION_REQUIRED"
    scenario = {
        "planning": [
            ModelResponse.final(
                {
                    "summary": f"Diagnose {len(issues)} findings and safely verify supported fixes",
                    "tasks": [{"id": "diagnose"}, {"id": "patch"}, {"id": "verify"}],
                    "acceptance_criteria": ["tests pass", "source repository remains unchanged"],
                    "risks": ["unsupported findings remain report-only"],
                }
            )
        ],
        "diagnosis": [ModelResponse.final({"outcome": outcome, "summary": f"Found {len(issues)} issues", "issues": issues})],
        "review": [
            ModelResponse.final(
                {
                    "summary": "Deterministic verification completed",
                    "outcome": "COMPLETED",
                    "lessons": ["Generate a proposal before applying and verify by exit code"],
                }
            )
        ],
    }
    if operations:
        scenario["patch_generation"] = [
            ModelResponse.final({"summary": f"Apply {len(operations)} supported fixes", "operations": operations})
        ]
    return ScriptedFakeModelGateway(scenario)
