from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from devpilot.agents.model_gateway import ModelGateway
from devpilot.domain.models import AgentResult, AgentSpec, WorkspaceRef
from devpilot.errors import ModelGatewayError
from devpilot.services.budget import BudgetService
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
    ) -> AgentInvocation:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": spec.instructions},
            {"role": "user", "content": json.dumps(node_context, ensure_ascii=False)},
        ]
        tool_refs: list[str] = []
        budget = execution_budget
        total_prompt = 0
        total_completion = 0
        tool_rounds = 0
        repaired = False

        while True:
            budget = self.budget_service.reserve_llm(budget)
            response = self.gateway.complete(
                agent_id=spec.agent_id,
                messages=messages,
                tools=[] if repaired else self.tools.registry.schemas(spec.allowed_tools),
                output_model=output_model,
                timeout_seconds=spec.timeout_seconds,
            )
            total_prompt += response.usage.prompt_tokens
            total_completion += response.usage.completion_tokens
            budget = self.budget_service.settle_llm(
                budget,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )

            if response.tool_calls:
                if repaired:
                    raise ModelGatewayError("repair response attempted a tool call")
                if tool_rounds >= spec.max_tool_rounds:
                    raise ModelGatewayError("TOOL_ROUND_BUDGET_EXHAUSTED")
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
                    tool_result = self.tools.execute(
                        call.name,
                        call.arguments,
                        workspace=workspace,
                        allowed_tools=spec.allowed_tools,
                        agent_id=spec.agent_id,
                        operation_id=operation_id,
                        execution_budget=budget,
                    )
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
