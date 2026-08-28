from __future__ import annotations

import pytest

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.domain.models import TaskStatus
from devpilot.domain.replay import EvaluationDataset
from devpilot.service import TaskService
from devpilot.testing.repo import make_test_repo


def _no_action_gateway(runs: int = 1) -> ScriptedFakeModelGateway:
    return ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {
                        "summary": "inspect",
                        "tasks": [{"id": "inspect"}],
                        "acceptance_criteria": ["baseline remains green"],
                        "risks": [],
                    }
                )
                for _ in range(runs)
            ],
            "diagnosis": [
                ModelResponse.final(
                    {
                        "outcome": "NO_ACTION_REQUIRED",
                        "summary": "already correct",
                        "issues": [],
                    }
                )
                for _ in range(runs)
            ],
            "review": [
                ModelResponse.final(
                    {
                        "summary": "no changes",
                        "outcome": "NO_CHANGES",
                        "lessons": [],
                    }
                )
                for _ in range(runs)
            ],
        }
    )


def _approval_gateway(runs: int = 2) -> ScriptedFakeModelGateway:
    plan = {
        "summary": "fix helper",
        "tasks": [{"id": "change"}],
        "acceptance_criteria": ["tests pass"],
        "risks": [],
    }
    diagnosis = {
        "outcome": "ISSUE_FOUND",
        "summary": "password helper issue",
        "issues": [{"issue": "password-helper"}],
    }
    patch = {
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
    return ScriptedFakeModelGateway(
        {
            "planning": [ModelResponse.final(plan) for _ in range(runs)],
            "diagnosis": [ModelResponse.final(diagnosis) for _ in range(runs)],
            "patch_generation": [ModelResponse.final(patch) for _ in range(runs)],
            "review": [
                ModelResponse.final(
                    {
                        "summary": "verified",
                        "outcome": "COMPLETED",
                        "lessons": [],
                    }
                )
                for _ in range(runs)
            ],
        }
    )


def test_event_and_state_replay_are_deterministic_and_auditable(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_no_action_gateway()
    )
    try:
        state = service.create_task(repo, "inspect repository")

        first = service.replay_events(state["task_id"])
        second = service.replay_events(state["task_id"])
        state_replay = service.replay_state(state["task_id"])

        assert first["integrity_ok"] is True
        assert first["source_digest"] == second["source_digest"]
        assert first["event_count"] == len(
            service.event_history(state["task_id"], state["run_id"])
        )
        assert state_replay["consistent"] is True
        assert state_replay["state"] == service.get_state(state["task_id"])
        assert state_replay["state_digest"] != state_replay["event_digest"]
        assert len(service.replay_history(state["task_id"])) == 3
    finally:
        service.close()


def test_event_replay_detects_sequence_gap_without_mutating_projection(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_no_action_gateway()
    )
    try:
        state = service.create_task(repo, "inspect repository")
        before = service.get_state(state["task_id"])
        with service.control._lock, service.control._conn:
            service.control._conn.execute(
                "DELETE FROM event_outbox WHERE task_id=? AND run_id=? AND sequence_number=2",
                (state["task_id"], state["run_id"]),
            )
            service.control._conn.execute(
                "DELETE FROM execution_events WHERE task_id=? AND run_id=? AND sequence_number=2",
                (state["task_id"], state["run_id"]),
            )

        replay = service.replay_events(state["task_id"])

        assert replay["integrity_ok"] is False
        assert "SEQUENCE_GAP" in {issue["code"] for issue in replay["issues"]}
        assert service.get_state(state["task_id"]) == before
    finally:
        service.close()


def test_event_replay_detects_missing_first_sequence(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_no_action_gateway()
    )
    try:
        state = service.create_task(repo, "inspect repository")
        with service.control._lock, service.control._conn:
            service.control._conn.execute(
                "DELETE FROM event_outbox "
                "WHERE task_id=? AND run_id=? AND sequence_number=1",
                (state["task_id"], state["run_id"]),
            )
            service.control._conn.execute(
                "DELETE FROM execution_events "
                "WHERE task_id=? AND run_id=? AND sequence_number=1",
                (state["task_id"], state["run_id"]),
            )

        replay = service.replay_events(state["task_id"])

        first_issue = next(
            issue for issue in replay["issues"] if issue["code"] == "SEQUENCE_GAP"
        )
        assert replay["integrity_ok"] is False
        assert "expected sequence 1" in first_issue["detail"]
    finally:
        service.close()


def test_state_replay_can_select_historical_revision(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_no_action_gateway()
    )
    try:
        state = service.create_task(repo, "inspect repository")
        replay = service.replay_state(
            state["task_id"], state_revision=1
        )

        assert replay["state_revision"] == 1
        assert replay["state"]["task_id"] == state["task_id"]
        assert replay["consistent"] is True
    finally:
        service.close()


def test_recovery_fork_creates_an_isolated_task_and_lineage(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_approval_gateway()
    )
    try:
        source = service.create_task(repo, "fix helper")
        approval = source["pending_approval"]
        source = service.decide_approval(
            source["task_id"],
            decision="APPROVE",
            approval_id=approval["approval_id"],
            patch_hash=approval["patch_hash"],
            base_revision=approval["base_revision"],
            expected_revision=source["state_revision"],
        )
        recovery = service.recovery_points(source["task_id"])[0]

        fork = service.fork_recovery_point(
            source["task_id"], recovery["recovery_point_id"]
        )
        target = service.get_state(fork["target_task_id"])

        assert fork["source_task_id"] == source["task_id"]
        assert fork["target_task_id"] != source["task_id"]
        assert target["parent_run_id"] == source["run_id"]
        assert target["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
        assert target["workspace_ref"]["worktree_ref"] != source["workspace_ref"][
            "worktree_ref"
        ]
        assert service.get_state(source["task_id"])["run_id"] == source["run_id"]
    finally:
        service.close()


def test_evaluation_report_metrics_history_and_comparison(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    dataset = EvaluationDataset.model_validate(
        {
            "name": "no-change-smoke",
            "version": "1",
            "cases": [
                {
                    "case_id": "clean-python",
                    "repo": str(repo),
                    "request": "inspect repository",
                    "expectation": {
                        "statuses": ["COMPLETED_NO_CHANGES"],
                        "changed_files": [],
                        "requires_approval": False,
                    },
                }
            ],
        }
    )
    gateway = _no_action_gateway(runs=3)
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        baseline = service.run_evaluation(
            dataset, prompt_version="prompt-v1"
        )
        candidate = service.run_evaluation(
            dataset,
            prompt_version="prompt-v2",
            prompt_overrides={"planning": "Candidate planning prompt."},
        )
        comparison = service.compare_evaluations(
            baseline["evaluation_id"], candidate["evaluation_id"]
        )

        assert baseline["metrics"]["average_score"] == 1
        assert baseline["metrics"]["status_accuracy"] == 1
        assert baseline["metrics"]["changed_files_f1"] == 1
        assert baseline["metrics"]["errored_cases"] == 0
        assert baseline["dataset"] == dataset.to_state_dict()
        assert service.evaluation_report(baseline["evaluation_id"]) == baseline
        assert len(service.evaluation_history()) == 2
        assert baseline["prompt_digest"] != candidate["prompt_digest"]
        planning_calls = [
            call for call in gateway.calls if call["agent_id"] == "planning"
        ]
        assert planning_calls[1]["messages"][0]["content"].startswith(
            "Candidate planning prompt."
        )
        assert comparison["winner"] == "TIE"
        assert comparison["metric_deltas"]["average_score"] == 0
        assert comparison["metric_deltas"]["changed_files_f1"] == 0

        different_dataset = dataset.model_copy(update={"version": "2"})
        incompatible = service.run_evaluation(different_dataset)
        with pytest.raises(ValueError, match="different datasets"):
            service.compare_evaluations(
                baseline["evaluation_id"], incompatible["evaluation_id"]
            )
    finally:
        service.close()


def test_evaluation_records_case_errors_instead_of_aborting(tmp_path):
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_no_action_gateway()
    )
    try:
        report = service.run_evaluation(
            {
                "name": "invalid-repo",
                "version": "1",
                "cases": [
                    {
                        "case_id": "missing",
                        "repo": str(tmp_path / "missing"),
                        "request": "inspect",
                        "expectation": {"statuses": ["FAILED"]},
                    }
                ],
            }
        )

        assert report["metrics"]["errored_cases"] == 1
        assert report["cases"][0]["score"] == 0
        assert report["cases"][0]["error"]
    finally:
        service.close()
