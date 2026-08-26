from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.agents.runner import AgentRunner
from devpilot.domain.models import AgentSpec, PlanDraft, WorkspaceRef
from devpilot.errors import ModelGatewayError, PolicyDeniedError, ToolExecutionError
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
        agent_id=None, operation_id="stable-op", execution_budget=ExecutionBudget().to_state_dict(),
    )
    assert len(attempts) == 2
    assert result is duplicate
    assert result.execution_budget["tool_calls_used"] == 2
    assert result.execution_budget["tool_retries_used"] == 1
