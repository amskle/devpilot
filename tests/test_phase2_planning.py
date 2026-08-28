import copy
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.domain.models import ExecutionBudget, PlanDocument, PlanDraft, ReplanRequest, TaskStatus
from devpilot.domain.plans import build_plan_document, create_replan_request, plan_reference
from devpilot.domain.state import validate_state
from devpilot.service import TaskService
from devpilot.testing.repo import make_test_repo as make_repo


def _commit_failing_expectation(repo: Path) -> None:
    (repo / "tests" / "test_basic.py").write_text(
        "import unittest\nfrom app import value\nclass TestValue(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(value, 3)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "expect target value"],
        check=True,
        capture_output=True,
    )


def test_initial_plan_is_a_versioned_document_with_active_lifecycle(tmp_path):
    repo = make_repo(tmp_path / "repo")
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {
                        "summary": "inspect safely",
                        "tasks": [{"id": "inspect", "title": "Inspect repository"}],
                        "acceptance_criteria": ["no unsupported edits"],
                        "risks": ["false positive"],
                    }
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "NO_ACTION_REQUIRED", "summary": "clean", "issues": []}
                )
            ],
            "review": [
                ModelResponse.final(
                    {"summary": "no changes", "outcome": "NO_CHANGES", "lessons": []}
                )
            ],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        state = service.create_task(repo, "inspect")
        assert state["status"] == TaskStatus.COMPLETED_NO_CHANGES.value
        assert state["active_plan_ref"]["plan_id"].startswith("plan_")
        assert state["active_plan_ref"]["version"] == 1

        raw = service.artifacts.read_text(
            state["task_id"], state["run_id"], state["active_plan_ref"]
        )
        document = PlanDocument.from_state_dict(json.loads(raw))
        assert document.version == 1
        assert document.parent_version is None
        assert document.repository_snapshot_id == state["workspace_ref"]["baseline_revision"]
        assert document.content_hash == state["active_plan_ref"]["content_hash"]

        history = service.plan_history(state["task_id"])
        assert len(history) == 1
        assert history[0]["document"] == document.to_state_dict()
        assert history[0]["lifecycle"]["status"] == "ACTIVE"
    finally:
        service.close()


def test_replan_reuses_planning_agent_and_atomically_switches_version(tmp_path):
    repo = make_repo(tmp_path / "repo")
    _commit_failing_expectation(repo)
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "plan v1", "tasks": [{"id": "fix"}], "acceptance_criteria": ["tests pass"], "risks": []}
                ),
                ModelResponse.final(
                    {"summary": "plan v2", "tasks": [{"id": "correct"}], "acceptance_criteria": ["value is 3"], "risks": []}
                ),
            ],
            "diagnosis": [
                ModelResponse.final({"outcome": "ISSUE_FOUND", "summary": "attempt one", "issues": [{"issue": "value"}]}),
                ModelResponse.final({"outcome": "ISSUE_FOUND", "summary": "attempt two", "issues": [{"issue": "value"}]}),
                ModelResponse.final({"outcome": "ISSUE_FOUND", "summary": "replanned", "issues": [{"issue": "value"}]}),
            ],
            "patch_generation": [
                ModelResponse.final(
                    {"summary": "one to two", "operations": [{"target_file": "app.py", "issues": ["value"], "replacements": [{"old": "value = 1", "new": "value = 2", "occurrence": 1}]}]}
                ),
                ModelResponse.final(
                    {"summary": "two to four", "operations": [{"target_file": "app.py", "issues": ["value"], "replacements": [{"old": "value = 2", "new": "value = 4", "occurrence": 1}]}]}
                ),
                ModelResponse.final(
                    {"summary": "two to three", "operations": [{"target_file": "app.py", "issues": ["value"], "replacements": [{"old": "value = 2", "new": "value = 3", "occurrence": 1}]}]}
                ),
            ],
            "review": [
                ModelResponse.final({"summary": "verified", "outcome": "COMPLETED", "lessons": []})
            ],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        waiting = service.create_task(
            repo,
            "set target value",
            budget=ExecutionBudget(max_iterations=1),
        )
        assert waiting["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value
        assert waiting["progress_window"]["entries"]
        first_ref = waiting["active_plan_ref"]

        final = service.replan(
            waiting["task_id"],
            waiting["state_revision"],
            reason="The original repair strategy did not satisfy the target test",
            idempotency_key="replan-once",
        )
        duplicate = service.replan(
            waiting["task_id"],
            waiting["state_revision"],
            reason="ignored duplicate payload",
            idempotency_key="replan-once",
        )

        assert final["status"] == TaskStatus.COMPLETED.value
        assert duplicate == final
        assert final["active_plan_ref"]["plan_id"] == first_ref["plan_id"]
        assert final["active_plan_ref"]["version"] == 2
        assert final["execution_budget"]["plan_revisions_used"] == 1
        assert final["progress_window"] == {"entries": [], "no_progress_rounds": 0}
        assert gateway.call_count("planning") == 2

        history = service.plan_history(final["task_id"])
        assert [item["document"]["version"] for item in history] == [1, 2]
        assert [item["lifecycle"]["status"] for item in history] == ["SUPERSEDED", "ACTIVE"]
        assert history[1]["document"]["parent_version"] == 1
        assert history[1]["document"]["change_reason"].startswith("The original repair strategy")

        requests = service.replan_history(final["task_id"])
        assert len(requests) == 1
        assert requests[0]["status"] == "CONSUMED"
        assert requests[0]["requested_from_plan_version"] == 1
        events = service.control.events(final["task_id"])
        assert sum(event["event_type"] == "replan_prepared" for event in events) == 1
        assert [event["payload"]["version"] for event in events if event["event_type"] == "plan_activated"] == [1, 2]
    finally:
        service.close()


def test_plan_invalid_diagnosis_automatically_runs_bounded_replanning(tmp_path):
    repo = make_repo(tmp_path / "repo")
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "assumption v1", "tasks": [{"id": "old"}], "acceptance_criteria": ["inspect"], "risks": []}
                ),
                ModelResponse.final(
                    {"summary": "evidence-based v2", "tasks": [{"id": "new"}], "acceptance_criteria": ["inspect safely"], "risks": []}
                ),
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "PLAN_INVALID", "summary": "The expected module is not present", "issues": []}
                ),
                ModelResponse.final(
                    {"outcome": "NO_ACTION_REQUIRED", "summary": "The revised plan is complete", "issues": []}
                ),
            ],
            "review": [
                ModelResponse.final({"summary": "reviewed", "outcome": "NO_CHANGES", "lessons": []})
            ],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        final = service.create_task(repo, "inspect assumptions")
        assert final["status"] == TaskStatus.COMPLETED_NO_CHANGES.value
        assert final["active_plan_ref"]["version"] == 2
        assert final["latest_failure"] is None
        assert final["execution_budget"]["plan_revisions_used"] == 1
        assert gateway.call_count("planning") == 2
        assert gateway.call_count("diagnosis") == 2
        requests = service.replan_history(final["task_id"])
        assert len(requests) == 1
        assert requests[0]["reason_code"] == "PLAN_INVALID"
        assert requests[0]["status"] == "CONSUMED"
        assert requests[0]["based_on_diagnosis_revision"] is not None
        assert service.plan_history(final["task_id"])[1]["document"]["based_on_diagnosis_revision"] == requests[0]["based_on_diagnosis_revision"]
    finally:
        service.close()


def test_plan_revision_budget_stops_before_another_planning_call(tmp_path):
    repo = make_repo(tmp_path / "repo")
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "plan", "tasks": [], "acceptance_criteria": ["inspect"], "risks": []}
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "PLAN_INVALID", "summary": "assumption failed", "issues": []}
                )
            ],
        },
        strict=False,
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        waiting = service.create_task(
            repo,
            "inspect assumptions",
            budget=ExecutionBudget(max_plan_revisions=0),
        )
        assert waiting["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value
        assert waiting["pause_reason"] == "PLAN_REVISION_BUDGET_EXHAUSTED"
        assert gateway.call_count("planning") == 1
        assert service.replan_history(waiting["task_id"]) == []
    finally:
        service.close()


def test_plan_activation_transaction_rolls_back_every_control_record(tmp_path):
    repo = make_repo(tmp_path / "repo")
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [ModelResponse.final({"summary": "v1", "tasks": [], "acceptance_criteria": ["clean"], "risks": []})],
            "diagnosis": [ModelResponse.final({"outcome": "NO_ACTION_REQUIRED", "summary": "clean", "issues": []})],
            "review": [ModelResponse.final({"summary": "done", "outcome": "NO_CHANGES", "lessons": []})],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        initial = service.create_task(repo, "inspect")
        first_document = PlanDocument.from_state_dict(
            json.loads(service.artifacts.read_text(initial["task_id"], initial["run_id"], initial["active_plan_ref"]))
        )
        request = create_replan_request(
            task_id=initial["task_id"],
            run_id=initial["run_id"],
            active_plan_ref=initial["active_plan_ref"],
            reason_code="PLAN_INVALID",
            summary="inject atomicity test",
            requested_at=service.clock.now().isoformat(),
        )
        prepared = copy.deepcopy(initial)
        prepared["pending_replan_request"] = request.to_state_dict()
        prepared["current_node"] = "prepare_replan"
        prepared = service.control.prepare_replan(
            validate_state(prepared),
            expected_revision=initial["state_revision"],
            request=request,
        )
        second_document = build_plan_document(
            PlanDraft(summary="v2", tasks=[], acceptance_criteria=["clean"], risks=[]),
            repository_snapshot_id=prepared["workspace_ref"]["current_revision"],
            created_at=service.clock.now().isoformat(),
            previous=first_document,
            replan_request=ReplanRequest.from_state_dict(prepared["pending_replan_request"]),
        )
        artifact = service.artifacts.put_json(
            prepared["task_id"], prepared["run_id"], "plan", second_document.to_state_dict()
        )
        ref = plan_reference(artifact, second_document)
        activating = copy.deepcopy(prepared)
        activating.update({"active_plan_ref": ref, "pending_replan_request": None, "current_node": "planning"})

        with service.control._conn:
            service.control._conn.execute(
                f"""CREATE TRIGGER inject_plan_activation_failure
                    BEFORE UPDATE ON task_projection
                    WHEN NEW.state_revision = {prepared['state_revision'] + 1}
                    BEGIN SELECT RAISE(ABORT, 'injected activation failure'); END"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected activation failure"):
            service.control.activate_plan(
                validate_state(activating),
                expected_revision=prepared["state_revision"],
                document=second_document,
                artifact_ref=ref,
                replan_request_id=request.replan_request_id,
            )
        with service.control._conn:
            service.control._conn.execute("DROP TRIGGER inject_plan_activation_failure")

        assert [item["lifecycle"]["status"] for item in service.plan_history(initial["task_id"])] == ["ACTIVE"]
        assert service.replan_history(initial["task_id"])[0]["status"] == "PENDING"
        projection = service.control.get_task(initial["task_id"])
        assert projection["state_revision"] == prepared["state_revision"]
        assert projection["state"]["pending_replan_request"]["replan_request_id"] == request.replan_request_id
    finally:
        service.close()
