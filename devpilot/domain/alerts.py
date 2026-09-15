from __future__ import annotations

from typing import Any, Literal

from devpilot.domain.models import StrictModel


AlertRule = Literal[
    "BUDGET_EXHAUSTED",
    "TASK_FAILED",
    "APPROVAL_PENDING",
    "HUMAN_INTERVENTION",
]
AlertSeverity = Literal["WARNING", "CRITICAL"]


class AlertRecord(StrictModel):
    """A deduplicated alert derived deterministically from a settled task state.

    ``fingerprint`` identifies the trigger within a run, while ``alert_id`` is
    a deterministic digest of task, run and fingerprint so repeat evaluations
    of one run stay idempotent without suppressing later incidents.
    """

    alert_id: str
    task_id: str
    run_id: str
    rule: AlertRule
    severity: AlertSeverity
    summary: str
    evidence: dict[str, Any]
    fingerprint: str
    created_at: str
