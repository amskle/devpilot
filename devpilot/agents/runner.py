from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from devpilot.agents.model_gateway import ModelGateway
from devpilot import telemetry
from devpilot.domain.models import (
    AgentResult,
    AgentSpec,
    ExecutionBudget,
    ModelProfile,
    WorkspaceRef,
)
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

    @staticmethod
    def _estimate_prompt_tokens(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: str,
    ) -> int:
        """Estimate this request payload instead of reserving the model maximum.

        ASCII-heavy source and JSON are conservatively estimated at three
        characters per token; non-ASCII text is counted one-for-one to stay
        conservative for CJK. The actual provider usage remains authoritative
        during settlement.
        """

        payload = json.dumps(
            {"messages": messages, "tools": tools, "output_schema": output_schema},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ascii_characters = sum(ord(character) < 128 for character in payload)
        non_ascii_characters = len(payload) - ascii_characters
        framing_tokens = 8 * (len(messages) + len(tools) + 1)
        return max(
            1,
            math.ceil(ascii_characters / 3)
            + non_ascii_characters
            + framing_tokens,
        )

    @staticmethod
    def _final_only_instruction(reason: str, output_schema: str) -> str:
        return (
            f"{reason} Do not call tools again. Using only evidence already "
            "returned, provide exactly one raw JSON object matching this schema, "
            f"without markdown fences or prose: {output_schema}"
        )

    @staticmethod
    def _parse_json(content: str) -> Any:
        """Remove one complete Markdown fence before strict JSON parsing."""

        stripped = content.strip()
        lines = stripped.splitlines()
        if (
            len(lines) >= 3
            and lines[0].strip().lower() in {"```", "```json"}
            and lines[-1].strip() == "```"
        ):
            stripped = "\n".join(lines[1:-1]).strip()
        return json.loads(stripped)

    @staticmethod
    def _repair_retrieval_evidence(
        raw: Any,
        available: dict[str, dict[str, Any]],
    ) -> tuple[Any, int]:
        """Replace cited evidence with trusted canonical fields and drop inventions."""

        if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), list):
            return raw, 0
        repaired: list[dict[str, Any]] = []
        seen: set[str] = set()
        changes = 0
        supplied_evidence = bool(raw["evidence"])
        for item in raw["evidence"]:
            citation = item.get("citation") if isinstance(item, dict) else None
            canonical = available.get(str(citation)) if citation else None
            if canonical is None or str(citation) in seen:
                changes += 1
                continue
            seen.add(str(citation))
            repaired.append(canonical)
            if item != canonical:
                changes += 1
        if supplied_evidence and not repaired and len(available) == 1:
            repaired.append(next(iter(available.values())))
        if not changes:
            return raw, 0
        return {**raw, "evidence": repaired}, changes

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
        node: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        attempt: int = 1,
    ) -> AgentInvocation:
        with telemetry.agent_observation(
            agent_id=spec.agent_id,
            task_id=task_id or "",
            run_id=run_id or "",
            node=node or "",
            attempt=attempt,
            context_keys=sorted(node_context),
        ) as observation:
            result = self._invoke_loop(
                spec,
                node_context=node_context,
                output_model=output_model,
                workspace=workspace,
                execution_budget=execution_budget,
                model_profile=model_profile,
                pricing_catalog=pricing_catalog,
                node=node,
                observation=observation,
            )
            observation.update(
                output={
                    "status": result.result.status,
                    "summary": result.result.summary,
                    "structured_output": result.result.structured_output,
                    "error": result.result.error,
                },
                metadata=telemetry.observation_output_metadata(result.result.structured_output),
                level="ERROR" if result.result.status != "ok" else "DEFAULT",
            )
        return result

    def _invoke_loop(
        self,
        spec: AgentSpec,
        *,
        node_context: dict[str, Any],
        output_model: type[BaseModel],
        workspace: WorkspaceRef,
        execution_budget: dict[str, Any],
        model_profile: ModelProfile | None,
        pricing_catalog: PricingCatalog | None,
        node: str | None,
        observation: Any,
    ) -> AgentInvocation:
        output_schema = json.dumps(
            output_model.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        retrieval_contract = ""
        if "repo-retrieval" in spec.allowed_tools:
            retrieval_contract = (
                "\n\nEvidence contract: a valid evidence item copies path, start_line, "
                "end_line, citation, content_sha256, and repository_revision exactly "
                "from one repo-retrieval match. A guessed citation or any changed "
                "field is invalid; omit it rather than inventing evidence."
            )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"{spec.instructions}\n\n"
                    "Trusted runtime rule: workspace_id is bound by DevPilot. "
                    "Do not invent, copy, or include workspace_id in tool arguments.\n\n"
                    "Output contract: after any tool use, return exactly one JSON object "
                    "matching the following JSON Schema. Use the schema field names exactly, "
                    "do not add prose or alternate field names, and do not call more tools once "
                    f"you have enough evidence.{retrieval_contract} Schema: {output_schema}"
                ),
            },
            {"role": "user", "content": json.dumps(node_context, ensure_ascii=False)},
        ]
        tool_refs: list[str] = []
        budget = execution_budget
        total_prompt = 0
        total_completion = 0
        tool_rounds = 0
        generations_used = 0
        tools_disabled = False
        schema_repaired = False
        retrieval_called = False
        retrieval_evidence: dict[str, dict[str, Any]] = {}
        policy_version = int(execution_budget.get("policy_version", 1))
        node_token_limit = (
            spec.max_token_budget if policy_version >= 2 else None
        )

        def require_final(reason: str) -> None:
            nonlocal tools_disabled
            if tools_disabled:
                return
            tools_disabled = True
            messages.append(
                {
                    "role": "user",
                    "content": self._final_only_instruction(reason, output_schema),
                }
            )

        def estimate_reservation(
            tool_schemas: list[dict[str, Any]],
        ) -> tuple[int, str]:
            if model_profile is None:
                return 0, "0"
            estimated_prompt_tokens = min(
                model_profile.max_prompt_tokens,
                self._estimate_prompt_tokens(
                    messages,
                    tool_schemas,
                    output_schema,
                ),
            )
            estimated_tokens = (
                estimated_prompt_tokens + model_profile.max_completion_tokens
            )
            estimated_cost = "0"
            if pricing_catalog is not None:
                estimated_cost = pricing_catalog.cost(
                    model_profile.model,
                    estimated_prompt_tokens,
                    model_profile.max_completion_tokens,
                )
            return estimated_tokens, estimated_cost

        while True:
            if generations_used + 1 >= spec.max_generations:
                require_final(
                    "This is the final generation allowed for this agent invocation."
                )
            tool_schemas = (
                []
                if tools_disabled
                else self.tools.registry.schemas(
                    spec.allowed_tools,
                    expose_runtime_fields=False,
                )
            )
            estimated_tokens, estimated_cost = estimate_reservation(tool_schemas)
            current_budget = ExecutionBudget.from_state_dict(budget)
            global_tokens_used = (
                current_budget.prompt_tokens_used
                + current_budget.completion_tokens_used
            )
            node_tokens_used = total_prompt + total_completion
            if (
                not tools_disabled
                and model_profile is not None
                and policy_version >= 2
            ):
                needs_final_headroom = (
                    global_tokens_used + (2 * estimated_tokens)
                    > current_budget.max_total_tokens
                )
                if node_token_limit is not None:
                    needs_final_headroom = needs_final_headroom or (
                        node_tokens_used + (2 * estimated_tokens)
                        > node_token_limit
                    )
                if needs_final_headroom:
                    require_final(
                        "The remaining token budget permits only a final response."
                    )
                    tool_schemas = []
                    estimated_tokens, estimated_cost = estimate_reservation(
                        tool_schemas
                    )
            if node_token_limit is not None:
                self.budget_service.ensure_token_capacity(
                    budget,
                    tokens_used=node_tokens_used,
                    estimated_tokens=estimated_tokens,
                    max_tokens=node_token_limit,
                    scope=f"{spec.agent_id} node",
                )
            budget = self.budget_service.reserve_llm(
                budget,
                estimated_tokens=estimated_tokens,
                estimated_cost=estimated_cost,
            )
            started = time.monotonic()
            turn = generations_used + 1
            try:
                response = self.gateway.complete(
                    agent_id=spec.agent_id,
                    messages=messages,
                    tools=tool_schemas,
                    output_model=output_model,
                    timeout_seconds=spec.timeout_seconds,
                    max_completion_tokens=(
                        model_profile.max_completion_tokens
                        if model_profile is not None
                        else None
                    ),
                    node=node,
                    turn=turn,
                )
            except Exception as exc:
                budget = self.budget_service.settle_active_time(budget, time.monotonic() - started)
                setattr(exc, "execution_budget", budget)
                raise
            generations_used += 1
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
            if node_token_limit is not None:
                self.budget_service.ensure_token_capacity(
                    budget,
                    tokens_used=total_prompt + total_completion,
                    estimated_tokens=0,
                    max_tokens=node_token_limit,
                    scope=f"{spec.agent_id} node",
                )
            observation.update(
                metadata={
                    "generations": generations_used,
                    "max_generations": spec.max_generations,
                    "tool_rounds": tool_rounds,
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_completion,
                    "node_token_budget": node_token_limit,
                    "budget_policy_version": policy_version,
                }
            )

            if response.tool_calls:
                if tools_disabled:
                    error = ModelGatewayError("final response attempted a tool call")
                    error.execution_budget = budget
                    raise error
                if tool_rounds >= spec.max_tool_rounds:
                    require_final(
                        "The bounded tool-round limit has been reached."
                    )
                    continue
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
                            node=node,
                        )
                    except Exception as exc:
                        if not hasattr(exc, "execution_budget"):
                            setattr(exc, "execution_budget", budget)
                        raise
                    budget = tool_result.execution_budget
                    tool_refs.append(operation_id)
                    if call.name == "repo-retrieval":
                        retrieval_called = True
                        repository_revision = tool_result.output.get(
                            "repository_revision"
                        )
                        for match in tool_result.output.get("matches") or []:
                            citation = match.get("citation")
                            if not citation:
                                continue
                            retrieval_evidence[str(citation)] = {
                                "path": match.get("path"),
                                "start_line": match.get("start_line"),
                                "end_line": match.get("end_line"),
                                "citation": citation,
                                "content_sha256": match.get("content_sha256"),
                                "repository_revision": repository_revision,
                            }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": json.dumps(tool_result.output, ensure_ascii=False),
                        }
                    )
                continue

            try:
                raw = self._parse_json(response.content or "")
                if retrieval_called:
                    raw, repaired_evidence = self._repair_retrieval_evidence(
                        raw,
                        retrieval_evidence,
                    )
                    if repaired_evidence:
                        observation.update(
                            metadata={
                                "repaired_evidence_items": repaired_evidence
                            }
                        )
                structured = output_model.model_validate(raw)
                self._validate_retrieval_evidence(
                    structured,
                    retrieval_called=retrieval_called,
                    available=retrieval_evidence,
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                if schema_repaired or generations_used >= spec.max_generations:
                    return AgentInvocation(
                        AgentResult(
                            status="error",
                            structured_output={},
                            summary="model output failed schema validation",
                            tool_call_refs=tool_refs,
                            token_usage={
                                "prompt": total_prompt,
                                "completion": total_completion,
                                "total": total_prompt + total_completion,
                            },
                            error={"code": "MODEL_OUTPUT_INVALID", "message": str(exc)},
                        ),
                        budget,
                    )
                schema_repaired = True
                tools_disabled = True
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response failed validation. Keep the same conclusion, "
                            "decision, and evidence as your previous response and fix only the format: "
                            "return exactly one raw JSON object at the top level for "
                            f"{output_model.__name__}, without markdown code fences, prose, or tool "
                            f"calls. Validation error: {exc}. JSON Schema: {output_schema}"
                        ),
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
                    token_usage={
                        "prompt": total_prompt,
                        "completion": total_completion,
                        "total": total_prompt + total_completion,
                    },
                    error=None,
                ),
                budget,
            )

    @staticmethod
    def _validate_retrieval_evidence(
        structured: BaseModel,
        *,
        retrieval_called: bool,
        available: dict[str, dict[str, Any]],
    ) -> None:
        """Reject citations that were not returned by this invocation's retriever."""

        if not retrieval_called:
            return
        evidence = getattr(structured, "evidence", [])
        if available and not evidence:
            raise ValueError(
                "repo-retrieval returned matches but final output contains no evidence"
            )
        for item in evidence:
            payload = item.model_dump(mode="json")
            expected = available.get(payload["citation"])
            if expected is None:
                raise ValueError(
                    f"evidence citation was not returned by repo-retrieval: {payload['citation']}"
                )
            if payload != expected:
                raise ValueError(
                    f"evidence does not match retrieved content: {payload['citation']}"
                )
