from __future__ import annotations

from decimal import Decimal
from typing import Any

from devpilot.domain.models import ExecutionBudget
from devpilot.errors import BudgetExceededError


class BudgetService:
    """Mutates a plain budget dictionary after validating a reserved operation."""

    def reserve_llm(self, raw: dict[str, Any], estimated_tokens: int = 0, estimated_cost: str = "0") -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        if budget.llm_calls_used + 1 > budget.max_llm_calls:
            raise BudgetExceededError("LLM call budget exhausted")
        if budget.prompt_tokens_used + budget.completion_tokens_used + estimated_tokens > budget.max_total_tokens:
            raise BudgetExceededError("token budget exhausted")
        if budget.max_cost is not None and Decimal(budget.cost_used) + Decimal(estimated_cost) > Decimal(budget.max_cost):
            raise BudgetExceededError("cost budget exhausted")
        update = budget.model_copy(update={"llm_calls_used": budget.llm_calls_used + 1})
        return update.to_state_dict()

    def settle_llm(
        self, raw: dict[str, Any], *, prompt_tokens: int, completion_tokens: int, actual_cost: str = "0"
    ) -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        total = budget.prompt_tokens_used + budget.completion_tokens_used + prompt_tokens + completion_tokens
        if total > budget.max_total_tokens:
            raise BudgetExceededError("actual token usage exceeded budget")
        update = budget.model_copy(
            update={
                "prompt_tokens_used": budget.prompt_tokens_used + prompt_tokens,
                "completion_tokens_used": budget.completion_tokens_used + completion_tokens,
                "cost_used": format(Decimal(budget.cost_used) + Decimal(actual_cost), "f"),
            }
        )
        return update.to_state_dict()

    def reserve_tool(self, raw: dict[str, Any], *, retry: bool = False) -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        if budget.tool_calls_used + 1 > budget.max_tool_calls:
            raise BudgetExceededError("tool call budget exhausted")
        if retry and budget.tool_retries_used + 1 > budget.max_tool_retries:
            raise BudgetExceededError("tool retry budget exhausted")
        return budget.model_copy(
            update={
                "tool_calls_used": budget.tool_calls_used + 1,
                "tool_retries_used": budget.tool_retries_used + (1 if retry else 0),
            }
        ).to_state_dict()
