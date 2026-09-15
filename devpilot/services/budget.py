from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from devpilot.domain.models import ExecutionBudget
from devpilot.errors import BudgetExceededError


class BudgetService:
    """Mutates a plain budget dictionary after validating a reserved operation."""

    @staticmethod
    def _exhausted(message: str, budget: ExecutionBudget) -> BudgetExceededError:
        """Create a budget error that preserves all usage settled so far."""

        error = BudgetExceededError(message)
        error.execution_budget = budget.to_state_dict()
        return error

    def reserve_llm(
        self,
        raw: dict[str, Any],
        estimated_tokens: int = 0,
        estimated_cost: str = "0",
    ) -> dict[str, Any]:
        estimated_cost_value = Decimal(estimated_cost)
        if (
            estimated_tokens < 0
            or not estimated_cost_value.is_finite()
            or estimated_cost_value < 0
        ):
            raise ValueError("estimated token and cost reservations must be non-negative")
        budget = ExecutionBudget.from_state_dict(raw)
        self._check_active_time(budget)
        if budget.llm_calls_used + 1 > budget.max_llm_calls:
            raise self._exhausted("LLM call budget exhausted", budget)
        tokens_used = budget.prompt_tokens_used + budget.completion_tokens_used
        if (
            tokens_used >= budget.max_total_tokens
            or tokens_used + estimated_tokens > budget.max_total_tokens
        ):
            raise self._exhausted("token budget exhausted", budget)
        if budget.max_cost is not None:
            cost_used = Decimal(budget.cost_used)
            cost_limit = Decimal(budget.max_cost)
            if (
                cost_used >= cost_limit
                or cost_used + estimated_cost_value > cost_limit
            ):
                raise self._exhausted("cost budget exhausted", budget)
        update = budget.model_copy(
            update={
                "llm_calls_used": budget.llm_calls_used + 1,
                "cost_used": self._format_cost(
                    Decimal(budget.cost_used) + estimated_cost_value
                ),
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
        reserved_cost_value = Decimal(reserved_cost)
        actual_cost_value = Decimal(actual_cost)
        if (
            prompt_tokens < 0
            or completion_tokens < 0
            or not reserved_cost_value.is_finite()
            or not actual_cost_value.is_finite()
            or reserved_cost_value < 0
            or actual_cost_value < 0
        ):
            raise ValueError("settled token and cost usage must be non-negative")
        budget = ExecutionBudget.from_state_dict(raw)
        total = budget.prompt_tokens_used + budget.completion_tokens_used + prompt_tokens + completion_tokens
        settled_cost = Decimal(budget.cost_used) - reserved_cost_value + actual_cost_value
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
            raise self._exhausted("tool call budget exhausted", budget)
        if retry and budget.tool_retries_used + 1 > budget.max_tool_retries:
            raise self._exhausted("tool retry budget exhausted", budget)
        return budget.model_copy(
            update={
                "tool_calls_used": budget.tool_calls_used + 1,
                "tool_retries_used": budget.tool_retries_used + (1 if retry else 0),
            }
        ).to_state_dict()

    def ensure_token_capacity(
        self,
        raw: dict[str, Any],
        *,
        tokens_used: int,
        estimated_tokens: int,
        max_tokens: int,
        scope: str,
    ) -> None:
        """Fail before or after a scoped operation without mutating global usage."""

        if min(tokens_used, estimated_tokens, max_tokens) < 0:
            raise ValueError("token capacity values must be non-negative")
        budget = ExecutionBudget.from_state_dict(raw)
        if tokens_used + estimated_tokens > max_tokens:
            raise self._exhausted(f"{scope} token budget exhausted", budget)

    def settle_active_time(self, raw: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
        budget = ExecutionBudget.from_state_dict(raw)
        elapsed = max(0, math.ceil(elapsed_seconds))
        return budget.model_copy(
            update={"active_seconds_used": budget.active_seconds_used + elapsed}
        ).to_state_dict()

    @staticmethod
    def _check_active_time(budget: ExecutionBudget) -> None:
        if budget.active_seconds_used >= budget.max_active_seconds:
            raise BudgetService._exhausted("active time budget exhausted", budget)

    @staticmethod
    def _format_cost(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.0001")), "f")
