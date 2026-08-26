from __future__ import annotations

import difflib
import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from devpilot.domain.models import WorkspaceRef
from devpilot.errors import PolicyDeniedError, ToolExecutionError
from devpilot.services.budget import BudgetService
from devpilot.workspace import WorkspaceManager
from skills.registry import run_skill, skill_metadata_path


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str


class ProjectContextInput(ToolInput):
    pass


class AnalysisInput(ToolInput):
    language: str = "auto"


class PatchGenerateInput(ToolInput):
    target_file: str
    replacements: list[dict[str, Any]]


class RiskAssessmentInput(ToolInput):
    diff: str
    changed_files: list[str]


class TestExecutionInput(ToolInput):
    command: str | None = None
    timeout: int = Field(default=120, ge=1, le=1800)


class KnowledgeExtractInput(ToolInput):
    notes: str
    tags: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel, WorkspaceRef], dict[str, Any]]
    side_effect: bool = False
    idempotent: bool = True
    retry_policy: Literal["NEVER", "BACKOFF"] = "NEVER"
    max_retries: int = 0
    allowed_agents: tuple[str, ...] = ()

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema


class ToolRegistry:
    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise PolicyDeniedError(f"unknown tool: {name}") from exc

    def schemas(
        self,
        names: tuple[str, ...] | list[str],
        *,
        expose_runtime_fields: bool = True,
    ) -> list[dict[str, Any]]:
        schemas = []
        for name in names:
            parameters = self.get(name).json_schema()
            if not expose_runtime_fields:
                parameters.get("properties", {}).pop("workspace_id", None)
                required = parameters.get("required", [])
                parameters["required"] = [field for field in required if field != "workspace_id"]
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": parameters,
                    },
                }
            )
        return schemas

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)


@dataclass
class ToolResult:
    operation_id: str
    output: dict[str, Any]
    attempts: int
    execution_budget: dict[str, Any]


class ToolExecutor:
    """The only owner of physical tool execution and tool-level retry."""

    def __init__(self, registry: ToolRegistry, budget_service: BudgetService | None = None):
        self.registry = registry
        self.budget_service = budget_service or BudgetService()
        self._completed: dict[str, ToolResult] = {}

    def execute(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        workspace: WorkspaceRef,
        allowed_tools: tuple[str, ...],
        agent_id: str | None,
        operation_id: str,
        execution_budget: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ToolResult:
        if operation_id in self._completed:
            cached = self._completed[operation_id]
            return ToolResult(
                cached.operation_id,
                cached.output,
                cached.attempts,
                execution_budget,
            )
        if name not in allowed_tools:
            raise PolicyDeniedError(f"tool {name} is not authorized")
        spec = self.registry.get(name)
        if agent_id is not None and spec.allowed_agents and agent_id not in spec.allowed_agents:
            raise PolicyDeniedError(f"agent {agent_id} cannot call {name}")
        if spec.side_effect and not spec.idempotent and not idempotency_key:
            retries = 0
        else:
            retries = spec.max_retries if spec.retry_policy == "BACKOFF" else 0
        model = spec.input_model.model_validate(inputs)
        if getattr(model, "workspace_id", None) != workspace.workspace_id:
            raise PolicyDeniedError("workspace_id does not match active workspace")

        budget = execution_budget
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            budget = self.budget_service.reserve_tool(budget, retry=attempt > 0)
            started = time.monotonic()
            try:
                output = spec.handler(model, workspace)
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                result = ToolResult(operation_id, output, attempt + 1, budget)
                self._completed[operation_id] = result
                return result
            except ToolExecutionError as exc:
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                last_error = exc
                if not exc.transient or attempt >= retries:
                    break
                time.sleep(min(0.02 * (2**attempt), 0.1))
            except (OSError, TimeoutError) as exc:
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(min(0.02 * (2**attempt), 0.1))
            except PolicyDeniedError as exc:
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                exc.execution_budget = budget
                raise
            except Exception as exc:
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                error = ToolExecutionError("TOOL_FAILED", str(exc))
                error.execution_budget = budget
                raise error from exc
        error = ToolExecutionError("TOOL_RETRY_EXHAUSTED", str(last_error or "tool failed"))
        error.execution_budget = budget
        raise error


def _legacy_repo_handler(skill_name: str) -> Callable[[BaseModel, WorkspaceRef], dict[str, Any]]:
    def handler(model: BaseModel, workspace: WorkspaceRef) -> dict[str, Any]:
        payload = model.model_dump(exclude={"workspace_id"})
        payload["repo_path"] = workspace.worktree_ref
        result = run_skill(skill_name, payload)
        if result.get("status") != "ok":
            raise ToolExecutionError("SKILL_FAILED", result.get("error", f"{skill_name} failed"))
        return result["data"]

    return handler


def _patch_handler(model: BaseModel, workspace: WorkspaceRef) -> dict[str, Any]:
    value = PatchGenerateInput.model_validate(model)
    target = WorkspaceManager.resolve_path(workspace, value.target_file)
    if not target.is_file():
        raise ToolExecutionError("TARGET_NOT_FOUND", f"target not found: {value.target_file}")
    original = target.read_text(encoding="utf-8", errors="ignore")
    content = original
    for replacement in value.replacements:
        old = str(replacement["old"])
        new = str(replacement["new"])
        occurrence = int(replacement.get("occurrence", 1))
        if occurrence < 1:
            raise ToolExecutionError("INVALID_REPLACEMENT", "occurrence must be >= 1")
        start = 0
        position = -1
        for _ in range(occurrence):
            position = content.find(old, start)
            if position < 0:
                raise ToolExecutionError("REPLACEMENT_TARGET_NOT_FOUND", old[:100])
            start = position + len(old)
        content = content[:position] + new + content[position + len(old) :]
    relative = target.relative_to(Path(workspace.worktree_ref).resolve()).as_posix()
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=f"a/{relative}", tofile=f"b/{relative}",
        )
    )
    return {"diff": diff, "new_content_hash": hashlib.sha256(content.encode()).hexdigest(), "applied": False}


def _risk_handler(model: BaseModel, workspace: WorkspaceRef) -> dict[str, Any]:
    value = RiskAssessmentInput.model_validate(model)
    forbidden = (".env", ".git/", "credentials", "id_rsa", "secrets/")
    lowered = [path.replace("\\", "/").lower() for path in value.changed_files]
    if any(any(marker in path for marker in forbidden) for path in lowered):
        return {"decision": "DENY", "level": "High", "score": 100, "reasons": ["forbidden or sensitive path"]}
    legacy = run_skill("risk-assessment", {"diff": value.diff})
    data = legacy["data"]
    decision = "AUTO_ALLOWED" if data["level"] == "Low" else "APPROVAL_REQUIRED"
    return {**data, "decision": decision}


def _test_handler(model: BaseModel, workspace: WorkspaceRef) -> dict[str, Any]:
    value = TestExecutionInput.model_validate(model)
    result = run_skill(
        "test-execution",
        {"cwd": workspace.worktree_ref, "command": value.command, "timeout": value.timeout},
    )
    if result.get("status") != "ok":
        raise ToolExecutionError("TEST_TOOL_FAILED", result.get("error", "test execution failed"))
    return result["data"]


def _knowledge_handler(model: BaseModel, workspace: WorkspaceRef) -> dict[str, Any]:
    value = KnowledgeExtractInput.model_validate(model)
    result = run_skill("knowledge-extract", {"notes": value.notes, "tags": value.tags})
    if result.get("status") != "ok":
        raise ToolExecutionError("SKILL_FAILED", result.get("error", "knowledge extraction failed"))
    return result["data"]


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec("project-context", ProjectContextInput, _legacy_repo_handler("project-context"), allowed_agents=("planning",)))
    registry.register(ToolSpec("code-analysis", AnalysisInput, _legacy_repo_handler("code-analysis"), allowed_agents=("diagnosis",)))
    registry.register(ToolSpec("bug-detection", AnalysisInput, _legacy_repo_handler("bug-detection"), allowed_agents=("diagnosis",)))
    registry.register(ToolSpec("security-scan", ProjectContextInput, _legacy_repo_handler("security-scan"), allowed_agents=("diagnosis",)))
    registry.register(ToolSpec("patch-generate", PatchGenerateInput, _patch_handler, allowed_agents=("patch_generation",)))
    registry.register(ToolSpec("risk-assessment", RiskAssessmentInput, _risk_handler))
    registry.register(ToolSpec("test-execution", TestExecutionInput, _test_handler, retry_policy="BACKOFF", max_retries=1))
    registry.register(ToolSpec("knowledge-extract", KnowledgeExtractInput, _knowledge_handler, allowed_agents=("review",)))
    return registry


def validate_skill_metadata(registry: ToolRegistry) -> list[str]:
    """metadata.yaml stays human-readable; executable schemas remain code-owned."""
    mismatches = []
    for name in registry.names:
        with skill_metadata_path(name).open("r", encoding="utf-8") as handle:
            metadata = yaml.safe_load(handle)
        if metadata.get("name") != name:
            mismatches.append(name)
    return mismatches
