from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from devpilot.domain.models import ExecutionBudget
from devpilot.errors import BudgetExceededError


class BudgetService:
    """Mutates a plain budget dictionary after validating a reserved operation."""

    def reserve_llm(self, raw: dict[str, Any], estimated_tokens: int = 0, estimated_cost: str = "0") -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        self._check_active_time(budget)
        if budget.llm_calls_used + 1 > budget.max_llm_calls:
            raise BudgetExceededError("LLM call budget exhausted")
        if budget.prompt_tokens_used + budget.completion_tokens_used + estimated_tokens > budget.max_total_tokens:
            raise BudgetExceededError("token budget exhausted")
        if budget.max_cost is not None and Decimal(budget.cost_used) + Decimal(estimated_cost) > Decimal(budget.max_cost):
            raise BudgetExceededError("cost budget exhausted")
        update = budget.model_copy(
            update={
                "llm_calls_used": budget.llm_calls_used + 1,
                "cost_used": self._format_cost(Decimal(budget.cost_used) + Decimal(estimated_cost)),
            }
        )
        return update.to_state_dict()

    def settle_llm(
        self,
        raw: dict[str, Any],
        *,
        prompt_tokens: int,
        completion_tokens: int,
        reserved_cost: str = "0",
        actual_cost: str = "0",
    ) -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        total = budget.prompt_tokens_used + budget.completion_tokens_used + prompt_tokens + completion_tokens
        settled_cost = Decimal(budget.cost_used) - Decimal(reserved_cost) + Decimal(actual_cost)
        if settled_cost < 0:
            raise ValueError("reserved cost exceeds current cost reservation")
        update = budget.model_copy(
            update={
                "prompt_tokens_used": budget.prompt_tokens_used + prompt_tokens,
                "completion_tokens_used": budget.completion_tokens_used + completion_tokens,
                "cost_used": self._format_cost(settled_cost),
            }
        )
        if total > budget.max_total_tokens:
            error = BudgetExceededError("actual token usage exceeded budget")
            error.execution_budget = update.to_state_dict()
            raise error
        if budget.max_cost is not None and settled_cost > Decimal(budget.max_cost):
            error = BudgetExceededError("actual cost exceeded budget")
            error.execution_budget = update.to_state_dict()
            raise error
        return update.to_state_dict()

    def reserve_tool(self, raw: dict[str, Any], *, retry: bool = False) -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        self._check_active_time(budget)
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

    def settle_active_time(self, raw: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        elapsed = max(0, math.ceil(elapsed_seconds))
        return budget.model_copy(
            update={"active_seconds_used": budget.active_seconds_used + elapsed}
        ).to_state_dict()

    @staticmethod
    def _check_active_time(budget: ExecutionBudget) -> None:
        if budget.active_seconds_used >= budget.max_active_seconds:
            raise BudgetExceededError("active time budget exhausted")

    @staticmethod
    def _format_cost(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.0001")), "f")
