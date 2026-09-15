from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.domain.models import ExecutionBudget, TaskStatus
from devpilot.domain.state import create_initial_state
from devpilot.service import TaskService
from devpilot.services.alerts import evaluate_alerts
from devpilot.services.storage import SQLiteControlStore
from devpilot.testing.repo import make_test_repo

CREATED_AT = "2026-01-01T00:00:00+00:00"


def _base_state():
    return create_initial_state("task_alerts", "run_alerts")


def _budget_paused_state(*, limit: int = 3, used: int = 3):
    state = _base_state()
    state["status"] = TaskStatus.WAITING_HUMAN_INTERVENTION.value
    state["pause_reason"] = "BUDGET_EXHAUSTED"
    state["latest_failure"] = {
        "error_code": "BUDGET_EXHAUSTED",
        "category": "BUDGET",
        "summary": "LLM call budget exhausted",
    }
    state["execution_budget"]["max_llm_calls"] = limit
    state["execution_budget"]["llm_calls_used"] = used
    return state


def _no_action_gateway() -> ScriptedFakeModelGateway:
    return ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {
                        "summary": "inspect safely",
                        "tasks": [{"id": "inspect"}],
                        "acceptance_criteria": ["no changes required"],
                        "risks": [],
                    }
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {
                        "outcome": "NO_ACTION_REQUIRED",
                        "summary": "already correct",
                        "issues": [],
                    }
                )
            ],
            "review": [
                ModelResponse.final(
                    {"summary": "done", "outcome": "NO_CHANGES", "lessons": []}
                )
            ],
        }
    )


def approval_scenario() -> ScriptedFakeModelGateway:
    return ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {
                        "summary": "plan",
                        "tasks": [],
                        "acceptance_criteria": ["tests pass"],
                        "risks": [],
                    }
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {
                        "outcome": "ISSUE_FOUND",
                        "summary": "password helper issue",
                        "issues": [{"issue": "password-helper"}],
                    }
                )
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "outcome": "PATCH",
                        "summary": "change password helper",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["password-helper"],
                                "replacements": [
                                    {
                                        "old": "value = 1",
                                        "new": "password_value = 1",
                                        "occurrence": 1,
                                    }
                                ],
                            }
                        ],
                    }
                )
            ],
            "review": [
                ModelResponse.final(
                    {"summary": "verified", "outcome": "COMPLETED", "lessons": ["safe"]}
                )
            ],
        }
    )


def test_evaluate_alerts_reports_exhausted_budget_dimensions():
    state = _budget_paused_state()

    alerts = evaluate_alerts(state, created_at=CREATED_AT)

    assert [alert.rule for alert in alerts] == [
        "BUDGET_EXHAUSTED",
        "HUMAN_INTERVENTION",
    ]
    alert = next(alert for alert in alerts if alert.rule == "BUDGET_EXHAUSTED")
    assert alert.severity == "CRITICAL"
    assert alert.fingerprint == "BUDGET_EXHAUSTED:llm_calls"
    assert alert.evidence == {"dimension": "llm_calls", "used": 3, "limit": 3}
    assert alert.created_at == CREATED_AT
    assert alert.task_id == "task_alerts"
    assert alert.run_id == "run_alerts"
    assert alert.alert_id.startswith("alert_")


def test_evaluate_alerts_ignores_healthy_and_completed_at_limit_states():
    healthy = _base_state()
    assert evaluate_alerts(healthy, created_at=CREATED_AT) == []

    completed = _base_state()
    completed["status"] = TaskStatus.COMPLETED.value
    completed["execution_budget"]["max_llm_calls"] = 3
    completed["execution_budget"]["llm_calls_used"] = 3
    assert evaluate_alerts(completed, created_at=CREATED_AT) == []


def test_zero_budget_is_zero_capacity_when_runtime_pauses():
    state = _budget_paused_state(limit=0, used=0)

    alerts = evaluate_alerts(state, created_at=CREATED_AT)

    budget_alert = next(alert for alert in alerts if alert.rule == "BUDGET_EXHAUSTED")
    assert budget_alert.evidence == {"dimension": "llm_calls", "used": 0, "limit": 0}


def test_evaluate_alerts_reports_failed_task_with_stable_fingerprint():
    state = _base_state()
    state["status"] = TaskStatus.FAILED.value
    state["latest_failure"] = {"error_code": "NODE_CRASH", "category": "NODE"}

    alerts = evaluate_alerts(state, created_at=CREATED_AT)

    assert [alert.rule for alert in alerts] == ["TASK_FAILED"]
    assert alerts[0].severity == "CRITICAL"
    assert alerts[0].fingerprint == "TASK_FAILED:NODE_CRASH"
    assert alerts[0].evidence["error_code"] == "NODE_CRASH"


def test_evaluate_alerts_reports_approval_and_human_intervention():
    approval_state = _base_state()
    approval_state["status"] = TaskStatus.WAITING_RISK_APPROVAL.value
    approval_state["pending_approval"] = {
        "approval_id": "approval_1",
        "patch_hash": "a" * 64,
        "expires_at": "2026-01-02T00:00:00+00:00",
    }

    approval_alerts = evaluate_alerts(approval_state, created_at=CREATED_AT)

    assert [alert.rule for alert in approval_alerts] == ["APPROVAL_PENDING"]
    assert approval_alerts[0].severity == "WARNING"
    assert approval_alerts[0].fingerprint == "APPROVAL_PENDING:approval_1"
    assert approval_alerts[0].evidence["approval_id"] == "approval_1"

    paused_state = _base_state()
    paused_state["status"] = TaskStatus.WAITING_HUMAN_INTERVENTION.value
    paused_state["pause_reason"] = "BUDGET_PAUSED"
    paused_state["latest_failure"] = {"error_code": "BUDGET_PAUSED", "category": "BUDGET"}

    paused_alerts = evaluate_alerts(paused_state, created_at=CREATED_AT)

    assert [alert.rule for alert in paused_alerts] == ["HUMAN_INTERVENTION"]
    assert paused_alerts[0].severity == "WARNING"
    assert paused_alerts[0].fingerprint == "HUMAN_INTERVENTION:BUDGET_PAUSED"
    assert paused_alerts[0].evidence["category"] == "BUDGET"


def test_alert_service_deduplicates_repeated_evaluation(tmp_path: Path) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        state = _budget_paused_state(limit=2, used=2)
        state["task_id"] = "task_dedupe"
        state["run_id"] = "run_dedupe"
        service.control.create_task(state)

        first = service.alert_service.evaluate_and_store(state)
        second = service.alert_service.evaluate_and_store(state)

        assert sorted(alert["rule"] for alert in first) == [
            "BUDGET_EXHAUSTED",
            "HUMAN_INTERVENTION",
        ]
        assert second == []
        history = service.alert_history("task_dedupe")
        assert len(history) == 2
        budget_alert = next(
            alert for alert in history if alert["rule"] == "BUDGET_EXHAUSTED"
        )
        first_budget_alert = next(
            alert for alert in first if alert["rule"] == "BUDGET_EXHAUSTED"
        )
        assert budget_alert["alert_id"] == first_budget_alert["alert_id"]
        assert budget_alert["fingerprint"] == "BUDGET_EXHAUSTED:llm_calls"
        assert budget_alert["evidence"] == {
            "dimension": "llm_calls",
            "used": 2,
            "limit": 2,
        }
    finally:
        service.close()


def test_approval_waiting_task_records_pending_alert(tmp_path: Path) -> None:
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=approval_scenario())
    try:
        state = service.create_task(repo, "fix helper")

        assert state["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
        history = service.alert_history(state["task_id"])
        assert [alert["rule"] for alert in history] == ["APPROVAL_PENDING"]
        assert history[0]["severity"] == "WARNING"
        assert history[0]["evidence"]["approval_id"] == (
            state["pending_approval"]["approval_id"]
        )

        assert service.alert_service.evaluate_and_store(state) == []
        assert len(service.alert_history(state["task_id"])) == 1
    finally:
        service.close()


def test_budget_exhaustion_records_alerts_for_paused_task(tmp_path: Path) -> None:
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        state = service.create_task(
            repo, "fix helper", budget=ExecutionBudget(max_llm_calls=1)
        )

        assert state["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value
        history = service.alert_history(state["task_id"])
        rules = sorted(alert["rule"] for alert in history)
        assert rules == ["BUDGET_EXHAUSTED", "HUMAN_INTERVENTION"]

        budget_alert = next(
            alert for alert in history if alert["rule"] == "BUDGET_EXHAUSTED"
        )
        assert budget_alert["severity"] == "CRITICAL"
        assert budget_alert["evidence"]["dimension"] == "llm_calls"
        assert budget_alert["evidence"]["used"] == 1
        assert budget_alert["evidence"]["limit"] == 1

        intervention = next(
            alert for alert in history if alert["rule"] == "HUMAN_INTERVENTION"
        )
        assert intervention["fingerprint"].startswith("HUMAN_INTERVENTION:")
        assert intervention["evidence"]["failure_code"] is not None
    finally:
        service.close()


def test_unknown_task_alert_history_raises_key_error(tmp_path: Path) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        with pytest.raises(KeyError):
            service.alert_history("task_missing")
        assert service.alert_history() == []
    finally:
        service.close()


def test_same_alert_can_be_recorded_again_in_a_new_run(tmp_path: Path) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        first_state = _budget_paused_state(limit=1, used=1)
        first_state["task_id"] = "task_runs"
        first_state["run_id"] = "run_one"
        service.control.create_task(first_state)
        first = service.alert_service.evaluate_and_store(first_state)

        second_state = {**first_state, "run_id": "run_two"}
        second = service.alert_service.evaluate_and_store(second_state)

        assert len(first) == 2
        assert len(second) == 2
        assert first[0]["alert_id"] != second[0]["alert_id"]
    finally:
        service.close()


def test_alert_projection_failure_does_not_replace_task_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    monkeypatch.setattr(
        service.alert_service,
        "evaluate_and_store",
        lambda state: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")),
    )
    try:
        state = service.create_task(make_test_repo(tmp_path / "repo"), "inspect")

        assert state["status"] == TaskStatus.COMPLETED_NO_CHANGES.value
    finally:
        service.close()


def test_legacy_alert_schema_migrates_to_run_scoped_deduplication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE alerts (
            alert_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT NOT NULL,
            rule TEXT NOT NULL, severity TEXT NOT NULL, summary TEXT NOT NULL,
            evidence_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(task_id, fingerprint)
        )"""
    )
    connection.execute(
        """INSERT INTO alerts VALUES
           ('alert_old', 'task_migration', 'run_one', 'TASK_FAILED', 'CRITICAL',
            'failed', '{}', 'TASK_FAILED:NODE_CRASH', ?)""",
        (CREATED_AT,),
    )
    connection.commit()
    connection.close()

    store = SQLiteControlStore(database)
    try:
        inserted = store.save_alert(
            {
                "alert_id": "alert_new",
                "task_id": "task_migration",
                "run_id": "run_two",
                "rule": "TASK_FAILED",
                "severity": "CRITICAL",
                "summary": "failed again",
                "evidence": {},
                "fingerprint": "TASK_FAILED:NODE_CRASH",
                "created_at": CREATED_AT,
            }
        )

        assert inserted is True
        assert len(store.list_alerts("task_migration")) == 2
    finally:
        store.close()


def test_deleting_terminal_task_removes_alerts(tmp_path: Path) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        state = create_initial_state("task_cleanup", "run_cleanup")
        state["status"] = TaskStatus.FAILED.value
        state["latest_failure"] = {
            "error_code": "NODE_CRASH",
            "category": "NODE",
        }
        service.control.create_task(state)
        assert len(service.alert_service.evaluate_and_store(state)) == 1

        service.delete_task("task_cleanup")

        count = service.control._conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE task_id=?", ("task_cleanup",)
        ).fetchone()[0]
        assert count == 0
    finally:
        service.close()
