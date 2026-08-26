from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from devpilot.agents.model_gateway import ModelGateway
from devpilot.domain.models import AgentResult, AgentSpec, ModelProfile, WorkspaceRef
from devpilot.errors import ModelGatewayError
from devpilot.services.budget import BudgetService
from devpilot.services.pricing import PricingCatalog
from devpilot.tools.executor import ToolExecutor


@dataclass(frozen=True)
class AgentInvocation:
    result: AgentResult
    execution_budget: dict[str, Any]


class AgentRunner:
    def __init__(self, gateway: ModelGateway, tools: ToolExecutor, budget_service: BudgetService | None = None):
        self.gateway = gateway
        self.tools = tools
        self.budget_service = budget_service or BudgetService()

    def invoke(
        self,
        spec: AgentSpec,
        *,
        node_context: dict[str, Any],
        output_model: type[BaseModel],
        workspace: WorkspaceRef,
        execution_budget: dict[str, Any],
        model_profile: ModelProfile | None = None,
        pricing_catalog: PricingCatalog | None = None,
    ) -> AgentInvocation:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"{spec.instructions}\n\n"
                    "Trusted runtime rule: workspace_id is bound by DevPilot. "
                    "Do not invent, copy, or include workspace_id in tool arguments."
                ),
            },
            {"role": "user", "content": json.dumps(node_context, ensure_ascii=False)},
        ]
        tool_refs: list[str] = []
        budget = execution_budget
        total_prompt = 0
        total_completion = 0
        tool_rounds = 0
        repaired = False
        estimated_tokens = 0
        estimated_cost = "0"
        if pricing_catalog is not None and model_profile is not None:
            estimated_tokens = model_profile.max_prompt_tokens + model_profile.max_completion_tokens
            estimated_cost = pricing_catalog.cost(
                model_profile.model,
                model_profile.max_prompt_tokens,
                model_profile.max_completion_tokens,
            )

        while True:
            budget = self.budget_service.reserve_llm(
                budget,
                estimated_tokens=estimated_tokens,
                estimated_cost=estimated_cost,
            )
            started = time.monotonic()
            try:
                response = self.gateway.complete(
                    agent_id=spec.agent_id,
                    messages=messages,
                    tools=(
                        []
                        if repaired
                        else self.tools.registry.schemas(
                            spec.allowed_tools,
                            expose_runtime_fields=False,
                        )
                    ),
                    output_model=output_model,
                    timeout_seconds=spec.timeout_seconds,
                )
            except Exception as exc:
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                setattr(exc, "execution_budget", budget)
                raise
            budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
            total_prompt += response.usage.prompt_tokens
            total_completion += response.usage.completion_tokens
            actual_cost = "0"
            if pricing_catalog is not None and model_profile is not None:
                actual_cost = pricing_catalog.cost(
                    model_profile.model,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )
            budget = self.budget_service.settle_llm(
                budget,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                reserved_cost=estimated_cost,
                actual_cost=actual_cost,
            )

            if response.tool_calls:
                if repaired:
                    error = ModelGatewayError("repair response attempted a tool call")
                    error.execution_budget = budget
                    raise error
                if tool_rounds >= spec.max_tool_rounds:
                    error = ModelGatewayError("TOOL_ROUND_BUDGET_EXHAUSTED")
                    error.execution_budget = budget
                    raise error
                tool_rounds += 1
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                            }
                            for call in response.tool_calls
                        ],
                    }
                )
                for call in response.tool_calls:
                    operation_id = f"{spec.agent_id}:{uuid.uuid4().hex}"
                    tool_inputs = {**call.arguments, "workspace_id": workspace.workspace_id}
                    try:
                        tool_result = self.tools.execute(
                            call.name,
                            tool_inputs,
                            workspace=workspace,
                            allowed_tools=spec.allowed_tools,
                            agent_id=spec.agent_id,
                            operation_id=operation_id,
                            execution_budget=budget,
                        )
                    except Exception as exc:
                        if not hasattr(exc, "execution_budget"):
                            setattr(exc, "execution_budget", budget)
                        raise
                    budget = tool_result.execution_budget
                    tool_refs.append(operation_id)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": json.dumps(tool_result.output, ensure_ascii=False),
                        }
                    )
                continue

            try:
                raw = json.loads(response.content or "")
                structured = output_model.model_validate(raw)
            except (json.JSONDecodeError, ValidationError) as exc:
                if repaired:
                    return AgentInvocation(
                        AgentResult(
                            status="error",
                            structured_output={},
                            summary="model output failed schema validation",
                            tool_call_refs=tool_refs,
                            token_usage={"prompt": total_prompt, "completion": total_completion},
                            error={"code": "MODEL_OUTPUT_INVALID", "message": str(exc)},
                        ),
                        budget,
                    )
                repaired = True
                messages.append(
                    {
                        "role": "user",
                        "content": f"Return only valid JSON for schema {output_model.__name__}. Previous output was invalid.",
                    }
                )
                continue

            value = structured.model_dump(mode="json")
            return AgentInvocation(
                AgentResult(
                    status="ok",
                    structured_output=value,
                    summary=str(value.get("summary", spec.role)),
                    tool_call_refs=tool_refs,
                    token_usage={"prompt": total_prompt, "completion": total_completion},
                    error=None,
                ),
                budget,
            )
