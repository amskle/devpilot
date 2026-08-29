from decimal import Decimal
from pathlib import Path
import subprocess

import pytest
from pydantic import BaseModel, ConfigDict

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.agents.runner import AgentRunner
from devpilot.domain.models import AgentSpec, ExecutionBudget, ModelProfile, PatchDraft, PlanDraft, WorkspaceRef
from devpilot.errors import BudgetExceededError, ModelGatewayError, PolicyDeniedError, ToolExecutionError
from devpilot.services.budget import BudgetService
from devpilot.services.pricing import ModelPrice, PricingCatalog
from devpilot.tools.executor import ToolExecutor, ToolInput, ToolRegistry, ToolSpec, build_default_registry


def _workspace(tmp_path: Path) -> WorkspaceRef:
    return WorkspaceRef(
        workspace_id="ws-test", repository_id="repo", worktree_ref=str(tmp_path),
        baseline_revision="a", current_revision="a", lease_owner="run", lease_expires_at="2099-01-01T00:00:00+00:00",
    )


def _spec(**updates) -> AgentSpec:
    values = {
        "agent_id": "planning", "role": "Planning", "instructions": "test",
        "allowed_tools": (), "output_schema": "PlanDraft", "model_profile": "fake",
    }
    values.update(updates)
    return AgentSpec(**values)


def test_scripted_fake_repairs_invalid_schema_once(tmp_path):
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final("not json"),
                ModelResponse.final(
                    {"summary": "ok", "tasks": [], "acceptance_criteria": ["pass"], "risks": []}
                ),
            ]
        }
    )
    runner = AgentRunner(gateway, ToolExecutor(build_default_registry()))
    result = runner.invoke(
        _spec(), node_context={}, output_model=PlanDraft, workspace=_workspace(tmp_path),
        execution_budget={
            "max_iterations": 3, "max_plan_revisions": 2, "max_rollbacks": 2, "max_llm_calls": 20,
            "max_tool_calls": 40, "max_tool_retries": 8, "max_total_tokens": 100000, "max_cost": None,
            "cost_currency": "USD", "max_active_seconds": 1800, "iterations_used": 0,
            "plan_revisions_used": 0, "rollbacks_used": 0, "llm_calls_used": 0, "tool_calls_used": 0,
            "tool_retries_used": 0, "prompt_tokens_used": 0, "completion_tokens_used": 0,
            "cost_used": "0.0000", "pricing_snapshot_ref": None, "active_seconds_used": 0,
        },
    )
    assert result.result.status == "ok"
    assert gateway.call_count("planning") == 2
    assert gateway.calls[-1]["tools"] == []
    gateway.assert_consumed()


def test_tool_round_limit_stops_before_execution(tmp_path):
    gateway = ScriptedFakeModelGateway(
        {"planning": [ModelResponse.tools([{"name": "project-context", "arguments": {"workspace_id": "ws-test"}}])]}
    )
    runner = AgentRunner(gateway, ToolExecutor(build_default_registry()))
    from devpilot.domain.models import ExecutionBudget
    with pytest.raises(ModelGatewayError, match="TOOL_ROUND_BUDGET_EXHAUSTED"):
        runner.invoke(
            _spec(allowed_tools=("project-context",), max_tool_rounds=0), node_context={}, output_model=PlanDraft,
            workspace=_workspace(tmp_path), execution_budget=ExecutionBudget().to_state_dict(),
        )


def test_unauthorized_tool_is_rejected_before_handler(tmp_path):
    executor = ToolExecutor(build_default_registry())
    from devpilot.domain.models import ExecutionBudget
    with pytest.raises(PolicyDeniedError):
        executor.execute(
            "security-scan", {"workspace_id": "ws-test"}, workspace=_workspace(tmp_path),
            allowed_tools=("project-context",), agent_id="planning", operation_id="op", execution_budget=ExecutionBudget().to_state_dict(),
        )

    with pytest.raises(PolicyDeniedError, match="workspace_id"):
        executor.execute(
            "project-context",
            {"workspace_id": "wrong-workspace"},
            workspace=_workspace(tmp_path),
            allowed_tools=("project-context",),
            agent_id="planning",
            operation_id="wrong-workspace",
            execution_budget=ExecutionBudget().to_state_dict(),
        )


def test_agent_runner_binds_workspace_id_instead_of_trusting_model(tmp_path):
    observed = []

    def handler(model, workspace):
        observed.append(model.workspace_id)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "bound-tool",
            ToolInput,
            handler,
            allowed_agents=("planning",),
        )
    )
    schema = registry.schemas(("bound-tool",), expose_runtime_fields=False)[0]
    assert "workspace_id" not in schema["function"]["parameters"]["properties"]
    assert "workspace_id" not in schema["function"]["parameters"]["required"]

    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.tools(
                    [{"name": "bound-tool", "arguments": {"workspace_id": "model-invented"}}]
                ),
                ModelResponse.final(
                    {"summary": "ok", "tasks": [], "acceptance_criteria": ["pass"], "risks": []}
                ),
            ]
        }
    )
    runner = AgentRunner(gateway, ToolExecutor(registry))
    result = runner.invoke(
        _spec(allowed_tools=("bound-tool",)),
        node_context={},
        output_model=PlanDraft,
        workspace=_workspace(tmp_path),
        execution_budget=ExecutionBudget().to_state_dict(),
    )
    assert result.result.status == "ok"
    assert observed == ["ws-test"]


def test_tool_executor_is_only_retry_owner(tmp_path):
    attempts = []

    def handler(model, workspace):
        attempts.append(1)
        if len(attempts) == 1:
            raise ToolExecutionError("TEMP", "try again", transient=True)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(ToolSpec("transient", ToolInput, handler, retry_policy="BACKOFF", max_retries=1))
    executor = ToolExecutor(registry)
    from devpilot.domain.models import ExecutionBudget
    result = executor.execute(
        "transient", {"workspace_id": "ws-test"}, workspace=_workspace(tmp_path), allowed_tools=("transient",),
        agent_id=None, operation_id="stable-op", execution_budget=ExecutionBudget().to_state_dict(),
    )
    duplicate = executor.execute(
        "transient", {"workspace_id": "ws-test"}, workspace=_workspace(tmp_path), allowed_tools=("transient",),
        agent_id=None,
        operation_id="stable-op",
        execution_budget=ExecutionBudget(tool_calls_used=7, tool_retries_used=2).to_state_dict(),
    )
    assert len(attempts) == 2
    assert result.execution_budget["tool_calls_used"] == 2
    assert result.execution_budget["tool_retries_used"] == 1
    assert duplicate.output == result.output
    assert duplicate.execution_budget["tool_calls_used"] == 7
    assert duplicate.execution_budget["tool_retries_used"] == 2


def test_non_transient_tool_error_keeps_original_code(tmp_path):
    attempts = []

    def handler(model, workspace):
        attempts.append(1)
        raise ToolExecutionError("REPLACEMENT_TARGET_NOT_FOUND", "missing exact source text")

    registry = ToolRegistry()
    registry.register(ToolSpec("exact-replacement", ToolInput, handler, retry_policy="BACKOFF", max_retries=2))
    executor = ToolExecutor(registry)
    with pytest.raises(ToolExecutionError) as captured:
        executor.execute(
            "exact-replacement",
            {"workspace_id": "ws-test"},
            workspace=_workspace(tmp_path),
            allowed_tools=("exact-replacement",),
            agent_id=None,
            operation_id="non-transient",
            execution_budget=ExecutionBudget().to_state_dict(),
        )
    assert captured.value.code == "REPLACEMENT_TARGET_NOT_FOUND"
    assert attempts == [1]


@pytest.mark.parametrize(
    "operations",
    [
        [],
        [
            {
                "target_file": "app.py",
                "replacements": [{"old": "value", "new": "value"}],
            }
        ],
        [
            {
                "target_file": "app.py",
                "replacements": [{"old": "one", "new": "two"}],
            },
            {
                "target_file": "app.py",
                "replacements": [{"old": "three", "new": "four"}],
            },
        ],
    ],
)
def test_patch_draft_rejects_non_applicable_operations(operations):
    with pytest.raises(ValueError):
        PatchDraft(summary="invalid patch", operations=operations)


@pytest.mark.parametrize(
    ("original", "old", "new", "expected_markers"),
    [
        ("value = 1", "value = 1", "value = 2", 2),
        ("value = 1", "value = 1", "value = 2\n", 1),
        ("value = 1\n", "value = 1\n", "value = 2", 1),
        ("first = 1\nlast = 2", "first = 1", "first = 2", 1),
    ],
)
def test_patch_generate_emits_git_diff_for_missing_final_newlines(
    tmp_path, original, old, new, expected_markers
):
    target = tmp_path / "app.py"
    target.write_text(original, encoding="utf-8")
    executor = ToolExecutor(build_default_registry())

    result = executor.execute(
        "patch-generate",
        {
            "workspace_id": "ws-test",
            "target_file": "app.py",
            "replacements": [
                {"old": old, "new": new, "occurrence": 1}
            ],
        },
        workspace=_workspace(tmp_path),
        allowed_tools=("patch-generate",),
        agent_id="patch_generation",
        operation_id="no-final-newline",
        execution_budget=ExecutionBudget().to_state_dict(),
    )

    patch = result.output["diff"]
    assert patch.count("\\ No newline at end of file") == expected_markers
    checked = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=tmp_path,
        input=patch.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr.decode(errors="replace")


def test_patch_generate_rejects_empty_direct_tool_replacements(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    executor = ToolExecutor(build_default_registry())

    with pytest.raises(ValueError):
        executor.execute(
            "patch-generate",
            {
                "workspace_id": "ws-test",
                "target_file": "app.py",
                "replacements": [],
            },
            workspace=_workspace(tmp_path),
            allowed_tools=("patch-generate",),
            agent_id="patch_generation",
            operation_id="empty-replacements",
            execution_budget=ExecutionBudget().to_state_dict(),
        )


def test_agent_runner_reserves_and_settles_model_cost(tmp_path):
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "ok", "tasks": [], "acceptance_criteria": ["pass"], "risks": []},
                    prompt_tokens=100,
                    completion_tokens=50,
                )
            ]
        }
    )
    runner = AgentRunner(gateway, ToolExecutor(build_default_registry()))
    catalog = PricingCatalog({"priced": ModelPrice(Decimal("10"), Decimal("20"))})
    result = runner.invoke(
        _spec(),
        node_context={},
        output_model=PlanDraft,
        workspace=_workspace(tmp_path),
        execution_budget=ExecutionBudget(max_cost="0.0100").to_state_dict(),
        model_profile=ModelProfile(
            provider="fake",
            model="priced",
            max_prompt_tokens=100,
            max_completion_tokens=100,
        ),
        pricing_catalog=catalog,
    )
    assert result.execution_budget["cost_used"] == "0.0020"
    assert result.execution_budget["active_seconds_used"] == 1


def test_cost_reservation_and_active_time_stop_calls_before_execution(tmp_path):
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "unused", "tasks": [], "acceptance_criteria": [], "risks": []}
                )
            ]
        },
        strict=False,
    )
    runner = AgentRunner(gateway, ToolExecutor(build_default_registry()))
    catalog = PricingCatalog({"priced": ModelPrice(Decimal("10"), Decimal("20"))})
    with pytest.raises(BudgetExceededError, match="cost budget"):
        runner.invoke(
            _spec(),
            node_context={},
            output_model=PlanDraft,
            workspace=_workspace(tmp_path),
            execution_budget=ExecutionBudget(max_cost="0.0010").to_state_dict(),
            model_profile=ModelProfile(
                provider="fake",
                model="priced",
                max_prompt_tokens=100,
                max_completion_tokens=100,
            ),
            pricing_catalog=catalog,
        )
    assert gateway.call_count() == 0

    with pytest.raises(BudgetExceededError, match="active time"):
        BudgetService().reserve_tool(ExecutionBudget(max_active_seconds=0).to_state_dict())


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_execution_budget_rejects_non_finite_or_negative_costs(value):
    with pytest.raises(ValueError, match="finite and non-negative"):
        ExecutionBudget(max_cost=value)


def test_pricing_and_usage_reject_negative_values():
    with pytest.raises(ValueError, match="model prices"):
        ModelPrice(Decimal("-1"), Decimal("1"))
    with pytest.raises(ValueError, match="token usage"):
        PricingCatalog(
            {"priced": ModelPrice(Decimal("1"), Decimal("1"))}
        ).cost("priced", -1, 0)
