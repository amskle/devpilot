"""Deterministic alert evaluation for settled task states.

Alerts are derived from one GraphState plus a timestamp; they never change the
state or append events. Persistence deduplicates by
``(task_id, run_id, fingerprint)`` so repeated evaluation of one run cannot
produce duplicate noise while a later restored run can report a new incident.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from devpilot.clock import Clock
from devpilot.domain.alerts import AlertRecord, AlertRule, AlertSeverity
from devpilot.domain.models import ExecutionBudget, TaskStatus
from devpilot.domain.state import GraphState
from devpilot.services.storage import SQLiteControlStore


def _alert_id(task_id: str, run_id: str, fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{task_id}:{run_id}:{fingerprint}".encode()
    ).hexdigest()
    return f"alert_{digest[:16]}"


def _record(
    state: GraphState,
    *,
    rule: AlertRule,
    severity: AlertSeverity,
    summary: str,
    evidence: dict[str, Any],
    fingerprint: str,
    created_at: str,
) -> AlertRecord:
    return AlertRecord(
        alert_id=_alert_id(state["task_id"], state["run_id"], fingerprint),
        task_id=state["task_id"],
        run_id=state["run_id"],
        rule=rule,
        severity=severity,
        summary=summary,
        evidence=evidence,
        fingerprint=fingerprint,
        created_at=created_at,
    )


def _budget_alerts(state: GraphState, *, created_at: str) -> list[AlertRecord]:
    """Report only budget dimensions that caused a settled task to pause.

    Merely reaching a limit is not an incident: a task may complete after using
    its final allowed call. The runtime records budget failures as a human pause
    with either a BUDGET failure category or an explicit budget pause reason.
    """

    if state["status"] != TaskStatus.WAITING_HUMAN_INTERVENTION.value:
        return []
    failure = state["latest_failure"] or {}
    pause_reason = str(state["pause_reason"] or "")
    failure_summary = str(failure.get("summary") or "").lower()
    has_budget_signal = (
        failure.get("category") == "BUDGET" or "BUDGET" in pause_reason.upper()
    )
    if not has_budget_signal:
        return []

    budget = ExecutionBudget.from_state_dict(state["execution_budget"])
    checks = [
        ("iterations", budget.iterations_used, budget.max_iterations),
        ("plan_revisions", budget.plan_revisions_used, budget.max_plan_revisions),
        ("rollbacks", budget.rollbacks_used, budget.max_rollbacks),
        ("llm_calls", budget.llm_calls_used, budget.max_llm_calls),
        ("tool_calls", budget.tool_calls_used, budget.max_tool_calls),
        ("tool_retries", budget.tool_retries_used, budget.max_tool_retries),
        (
            "total_tokens",
            budget.prompt_tokens_used + budget.completion_tokens_used,
            budget.max_total_tokens,
        ),
        ("active_seconds", budget.active_seconds_used, budget.max_active_seconds),
    ]
    signalled_dimensions: set[str] | None = None
    if failure.get("category") == "BUDGET":
        message_dimensions = (
            ("tool retry", "tool_retries"),
            ("tool call", "tool_calls"),
            ("llm call", "llm_calls"),
            ("token", "total_tokens"),
            ("active time", "active_seconds"),
            ("cost", "cost"),
        )
        for marker, dimension in message_dimensions:
            if marker in failure_summary:
                signalled_dimensions = {dimension}
                break
    if pause_reason == "PLAN_REVISION_BUDGET_EXHAUSTED":
        signalled_dimensions = {"plan_revisions"}
    elif pause_reason == "NO_PROGRESS_OR_BUDGET" and (
        budget.iterations_used >= budget.max_iterations
    ):
        signalled_dimensions = {"iterations"}

    records: list[AlertRecord] = []
    for dimension, used, limit in checks:
        if used >= limit and (
            signalled_dimensions is None or dimension in signalled_dimensions
        ):
            records.append(
                _record(
                    state,
                    rule="BUDGET_EXHAUSTED",
                    severity="CRITICAL",
                    summary=f"budget dimension exhausted: {dimension} ({used}/{limit})",
                    evidence={"dimension": dimension, "used": used, "limit": limit},
                    fingerprint=f"BUDGET_EXHAUSTED:{dimension}",
                    created_at=created_at,
                )
            )
    if budget.max_cost is not None and (
        signalled_dimensions is None or "cost" in signalled_dimensions
    ):
        cost_limit = Decimal(budget.max_cost)
        if Decimal(budget.cost_used) >= cost_limit:
            records.append(
                _record(
                    state,
                    rule="BUDGET_EXHAUSTED",
                    severity="CRITICAL",
                    summary=f"budget dimension exhausted: cost ({budget.cost_used}/{budget.max_cost})",
                    evidence={
                        "dimension": "cost",
                        "used": budget.cost_used,
                        "limit": budget.max_cost,
                        "currency": budget.cost_currency,
                    },
                    fingerprint="BUDGET_EXHAUSTED:cost",
                    created_at=created_at,
                )
            )
    return records


def _approval_alerts(state: GraphState, *, created_at: str) -> list[AlertRecord]:
    if state["status"] != TaskStatus.WAITING_RISK_APPROVAL.value:
        return []
    approval = state["pending_approval"] or {}
    approval_id = str(approval.get("approval_id", "unknown"))
    return [
        _record(
            state,
            rule="APPROVAL_PENDING",
            severity="WARNING",
            summary=f"risk approval pending: {approval_id}",
            evidence={
                "approval_id": approval_id,
                "expires_at": approval.get("expires_at"),
                "patch_hash": approval.get("patch_hash"),
            },
            fingerprint=f"APPROVAL_PENDING:{approval_id}",
            created_at=created_at,
        )
    ]


def _human_intervention_alerts(
    state: GraphState, *, created_at: str
) -> list[AlertRecord]:
    if state["status"] != TaskStatus.WAITING_HUMAN_INTERVENTION.value:
        return []
    failure = state["latest_failure"] or {}
    pause_reason = str(state["pause_reason"] or failure.get("error_code") or "UNKNOWN")
    return [
        _record(
            state,
            rule="HUMAN_INTERVENTION",
            severity="WARNING",
            summary=f"task paused for human intervention: {pause_reason}",
            evidence={
                "pause_reason": pause_reason,
                "failure_code": failure.get("error_code"),
                "category": failure.get("category"),
            },
            fingerprint=f"HUMAN_INTERVENTION:{pause_reason}",
            created_at=created_at,
        )
    ]


def _failed_alerts(state: GraphState, *, created_at: str) -> list[AlertRecord]:
    if state["status"] != TaskStatus.FAILED.value:
        return []
    failure = state["latest_failure"] or {}
    error_code = str(failure.get("error_code", "UNKNOWN"))
    return [
        _record(
            state,
            rule="TASK_FAILED",
            severity="CRITICAL",
            summary=f"task failed: {error_code}",
            evidence={
                "error_code": error_code,
                "category": failure.get("category"),
            },
            fingerprint=f"TASK_FAILED:{error_code}",
            created_at=created_at,
        )
    ]


def evaluate_alerts(state: GraphState, *, created_at: str) -> list[AlertRecord]:
    """Return every alert implied by one settled state, in stable order."""

    return [
        *_budget_alerts(state, created_at=created_at),
        *_failed_alerts(state, created_at=created_at),
        *_approval_alerts(state, created_at=created_at),
        *_human_intervention_alerts(state, created_at=created_at),
    ]


class AlertService:
    """Evaluate alert rules against settled states and persist them deduplicated."""

    def __init__(self, store: SQLiteControlStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock

    def evaluate_and_store(self, state: GraphState) -> list[dict[str, Any]]:
        """Persist alerts for this state and return only newly created records."""

        created_at = self.clock.now().isoformat()
        created: list[dict[str, Any]] = []
        for record in evaluate_alerts(state, created_at=created_at):
            payload = record.to_state_dict()
            if self.store.save_alert(payload):
                created.append(payload)
        return created

    def history(
        self, task_id: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        return self.store.list_alerts(task_id, limit=limit)
