import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.clock import FrozenClock
from devpilot.domain.models import TaskStatus
from devpilot.domain.models import ExecutionBudget
from devpilot.errors import StateConflictError
from devpilot.service import TaskService
from devpilot.testing.repo import make_test_repo as make_repo


def approval_scenario() -> ScriptedFakeModelGateway:
    return ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final({"summary": "plan", "tasks": [], "acceptance_criteria": ["tests pass"], "risks": []})
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "ISSUE_FOUND", "summary": "password helper issue", "issues": [{"issue": "password-helper"}]}
                )
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "summary": "change password helper",
                        "operations": [
                            {
                                "target_file": "app.py", "issues": ["password-helper"],
                                "replacements": [{"old": "value = 1", "new": "password_value = 1", "occurrence": 1}],
                            }
                        ],
                    }
                )
            ],
            "review": [
                ModelResponse.final({"summary": "verified", "outcome": "COMPLETED", "lessons": ["safe"]})
            ],
        }
    )


def no_action_scenario() -> ScriptedFakeModelGateway:
    return ScriptedFakeModelGateway(
        {
            "planning": [ModelResponse.final({"summary": "plan", "tasks": [], "acceptance_criteria": ["clean"], "risks": []})],
            "diagnosis": [ModelResponse.final({"outcome": "NO_ACTION_REQUIRED", "summary": "clean", "issues": []})],
            "review": [ModelResponse.final({"summary": "no changes", "outcome": "NO_CHANGES", "lessons": []})],
        }
    )


def test_graph_interrupts_and_resumes_bound_approval(tmp_path):
    repo = make_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=approval_scenario())
    try:
        state = service.create_task(repo, "fix helper")
        assert state["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
        assert state["patch_proposal"]["status"] == "WAITING_RISK_APPROVAL"
        pending = state["pending_approval"]
        final = service.decide_approval(
            state["task_id"], decision="APPROVE", approval_id=pending["approval_id"],
            patch_hash=pending["patch_hash"], base_revision=pending["base_revision"],
            expected_revision=state["state_revision"],
        )
        assert final["status"] == TaskStatus.COMPLETED.value
        assert final["patch_proposal"]["status"] == "VERIFIED"
        assert (repo / "app.py").read_text(encoding="utf-8") == "value = 1\n"
        assert final["workspace_ref"]["current_revision"] != final["workspace_ref"]["baseline_revision"]
    finally:
        service.close()


def test_cli_only_approval_timeout_is_lazy(tmp_path):
    repo = make_repo(tmp_path / "repo")
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    service = TaskService(
        data_dir=tmp_path / "data", gateway=approval_scenario(), clock=clock, approval_ttl_seconds=1
    )
    try:
        state = service.create_task(repo, "fix helper")
        clock.advance(seconds=2)
        expired = service.get_state(state["task_id"])
        assert expired["status"] == TaskStatus.CANCELLED.value
        assert expired["pause_reason"] == "APPROVAL_EXPIRED"
        assert any(event["event_type"] == "approval_expired" for event in service.control.events(state["task_id"]))
    finally:
        service.close()


def test_approval_target_and_revision_are_bound(tmp_path):
    repo = make_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=approval_scenario())
    try:
        state = service.create_task(repo, "fix helper")
        pending = state["pending_approval"]
        try:
            service.decide_approval(
                state["task_id"], decision="APPROVE", approval_id=pending["approval_id"],
                patch_hash="wrong", base_revision=pending["base_revision"], expected_revision=state["state_revision"],
            )
            raise AssertionError("mismatched patch hash was accepted")
        except ValueError:
            pass
        assert service.get_state(state["task_id"])["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
        try:
            service.decide_approval(
                state["task_id"], decision="APPROVE", approval_id=pending["approval_id"],
                patch_hash=pending["patch_hash"], base_revision=pending["base_revision"], expected_revision=0,
            )
            raise AssertionError("stale revision was accepted")
        except StateConflictError:
            pass
    finally:
        service.close()


def test_cancel_is_persistently_idempotent(tmp_path):
    repo = make_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=approval_scenario())
    try:
        waiting = service.create_task(repo, "fix helper")
        cancelled = service.cancel(
            waiting["task_id"],
            waiting["state_revision"],
            idempotency_key="cancel-once",
        )
        duplicate = service.cancel(
            waiting["task_id"],
            waiting["state_revision"],
            idempotency_key="cancel-once",
        )
        assert cancelled["status"] == TaskStatus.CANCELLED.value
        assert cancelled["patch_proposal"]["status"] == "INVALIDATED"
        assert duplicate == cancelled
        events = service.control.events(waiting["task_id"])
        assert sum(event["event_type"] == "task_cancelled" for event in events) == 1
    finally:
        service.close()


def test_checkpoint_survives_service_restart(tmp_path):
    repo = make_repo(tmp_path / "repo")
    data = tmp_path / "data"
    first = TaskService(data_dir=data, gateway=no_action_scenario())
    final = first.create_task(repo, "inspect")
    first.close()
    second = TaskService(data_dir=data, gateway=ScriptedFakeModelGateway({}, strict=False))
    try:
        restored = second.get_state(final["task_id"])
        assert restored == final
        assert restored["status"] == TaskStatus.COMPLETED_NO_CHANGES.value
    finally:
        second.close()


def test_max_cost_without_catalog_price_fails_before_execution(tmp_path):
    repo = make_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=no_action_scenario())
    try:
        try:
            service.create_task(repo, "inspect", budget=ExecutionBudget(max_cost="1.0000"), model="missing")
            raise AssertionError("unknown model price was accepted")
        except ValueError as exc:
            assert "pricing data" in str(exc)
    finally:
        service.close()


def test_pricing_snapshot_is_frozen_and_actual_cost_is_settled(tmp_path):
    repo = make_repo(tmp_path / "repo")
    data = tmp_path / "data"
    pricing = data / "pricing"
    pricing.mkdir(parents=True)
    catalog_path = pricing / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "models": {
                    "priced": {
                        "prompt_per_million": "1000",
                        "completion_per_million": "2000",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    service = TaskService(data_dir=data, gateway=approval_scenario(), model="priced")
    try:
        waiting = service.create_task(
            repo,
            "fix helper",
            budget=ExecutionBudget(max_cost="50.0000"),
        )
        assert waiting["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
        assert len(waiting["execution_budget"]["pricing_snapshot_ref"]) == 64

        # A catalog update affects new tasks only. Resume must use the task's
        # immutable snapshot, otherwise the next reservation would exceed 50.
        catalog_path.write_text(
            json.dumps(
                {
                    "models": {
                        "priced": {
                            "prompt_per_million": "1000000",
                            "completion_per_million": "2000000",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        pending = waiting["pending_approval"]
        final = service.decide_approval(
            waiting["task_id"],
            decision="APPROVE",
            approval_id=pending["approval_id"],
            patch_hash=pending["patch_hash"],
            base_revision=pending["base_revision"],
            expected_revision=waiting["state_revision"],
        )
        assert final["status"] == TaskStatus.COMPLETED.value
        assert final["execution_budget"]["cost_used"] == "0.0120"
    finally:
        service.close()


def test_first_verification_failure_retries_and_success_clears_latest_failure(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "tests" / "test_basic.py").write_text(
        "import unittest\nfrom app import value\nclass TestValue(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(value, 3)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "expect target value"], check=True, capture_output=True)
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "plan", "tasks": [], "acceptance_criteria": ["value is 3"], "risks": []}
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "ISSUE_FOUND", "summary": "first", "issues": [{"issue": "value"}]}
                ),
                ModelResponse.final(
                    {"outcome": "ISSUE_FOUND", "summary": "second", "issues": [{"issue": "value"}]}
                ),
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "summary": "wrong attempt",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["value"],
                                "replacements": [{"old": "value = 1", "new": "value = 2", "occurrence": 1}],
                            }
                        ],
                    }
                ),
                ModelResponse.final(
                    {
                        "summary": "correct attempt",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["value"],
                                "replacements": [{"old": "value = 2", "new": "value = 3", "occurrence": 1}],
                            }
                        ],
                    }
                ),
            ],
            "review": [
                ModelResponse.final({"summary": "verified", "outcome": "COMPLETED", "lessons": []})
            ],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        final = service.create_task(repo, "set target value")
        assert final["status"] == TaskStatus.COMPLETED.value, final
        assert final["latest_failure"] is None
        assert final["patch_proposal"]["status"] == "VERIFIED"
        assert final["execution_budget"]["iterations_used"] == 1
        assert gateway.call_count("diagnosis") == 2
        tracked = subprocess.run(
            ["git", "-C", final["workspace_ref"]["worktree_ref"], "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "__pycache__" not in tracked
    finally:
        service.close()


def test_no_action_after_failed_patch_rolls_back_and_requires_human(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "tests" / "test_basic.py").write_text(
        "import unittest\nfrom app import value\nclass TestValue(unittest.TestCase):\n"
        "    def test_value(self): self.assertEqual(value, 3)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "expect target value"], check=True, capture_output=True)
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "plan", "tasks": [], "acceptance_criteria": ["value is 3"], "risks": []}
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "ISSUE_FOUND", "summary": "first", "issues": [{"issue": "value"}]}
                ),
                ModelResponse.final(
                    {"outcome": "NO_ACTION_REQUIRED", "summary": "cannot improve", "issues": []}
                ),
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "summary": "wrong attempt",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["value"],
                                "replacements": [{"old": "value = 1", "new": "value = 2", "occurrence": 1}],
                            }
                        ],
                    }
                )
            ],
        },
        strict=False,
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        waiting = service.create_task(repo, "set target value")
        assert waiting["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value
        assert waiting["patch_proposal"]["status"] == "INVALIDATED"
        assert waiting["review"] is None
        workspace_file = Path(waiting["workspace_ref"]["worktree_ref"]) / "app.py"
        assert workspace_file.read_text(encoding="utf-8") == "value = 1\n"
    finally:
        service.close()


def test_restore_forks_new_run_and_restores_git_plan_and_artifacts(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "tests" / "test_basic.py").write_text(
        "import unittest\nfrom app import value\nclass TestValue(unittest.TestCase):\n"
        "    def test_value(self): self.assertTrue(value == 3)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "expect target value"], check=True, capture_output=True)
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [ModelResponse.final({"summary": "plan", "tasks": [], "acceptance_criteria": ["tests pass"], "risks": []})],
            "diagnosis": [
                ModelResponse.final({"outcome": "ISSUE_FOUND", "summary": "first", "issues": [{"issue": "value"}]}),
                ModelResponse.final({"outcome": "ISSUE_FOUND", "summary": "second", "issues": [{"issue": "value"}]}),
                ModelResponse.final({"outcome": "ISSUE_FOUND", "summary": "restored", "issues": [{"issue": "value"}]}),
            ],
            "patch_generation": [
                ModelResponse.final({"summary": "wrong attempt", "operations": [{"target_file": "app.py", "issues": ["value"], "replacements": [{"old": "value = 1", "new": "value = 2", "occurrence": 1}]}]}),
                ModelResponse.final({"summary": "second wrong attempt", "operations": [{"target_file": "app.py", "issues": ["value"], "replacements": [{"old": "value = 2", "new": "value = 4", "occurrence": 1}]}]}),
                ModelResponse.final({"summary": "correct attempt", "operations": [{"target_file": "app.py", "issues": ["value"], "replacements": [{"old": "value = 2", "new": "value = 3", "occurrence": 1}]}]}),
            ],
            "review": [ModelResponse.final({"summary": "verified", "outcome": "COMPLETED", "lessons": []})],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        failed = service.create_task(
            repo,
            "set target value",
            budget=ExecutionBudget(max_iterations=1),
        )
        assert failed["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value
        recovery_raw = service.artifacts.read_text(
            failed["task_id"], failed["run_id"], {"sha256": failed["active_recovery_point_ref"]}
        )
        recovery_id = json.loads(recovery_raw)["recovery_point_id"]
        rolled_back = service.rollback(
            failed["task_id"],
            recovery_id,
            failed["state_revision"],
            idempotency_key="rollback-once",
        )
        duplicate_rollback = service.rollback(
            failed["task_id"],
            recovery_id,
            failed["state_revision"],
            idempotency_key="rollback-once",
        )
        assert duplicate_rollback == rolled_back
        restored = service.restore(failed["task_id"], recovery_id, idempotency_key="restore-once")
        assert restored["status"] == TaskStatus.COMPLETED.value, restored
        assert restored["parent_run_id"] == failed["run_id"]
        assert restored["run_id"] != failed["run_id"]
        assert restored["progress_window"] == {"entries": [], "no_progress_rounds": 0}
        duplicate = service.restore(failed["task_id"], recovery_id, idempotency_key="restore-once")
        assert duplicate["run_id"] == restored["run_id"]
        assert duplicate["state_revision"] == restored["state_revision"]
        assert (repo / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        service.close()


def test_node_exception_is_normalized_and_checkpointed(tmp_path):
    repo = make_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data",
        gateway=ScriptedFakeModelGateway({"planning": [TimeoutError("model timed out")]}, strict=False),
    )
    try:
        state = service.create_task(repo, "inspect")
        assert state["status"] == TaskStatus.FAILED.value
        assert state["latest_failure"]["category"] == "NODE"
        assert state["latest_failure"]["error_code"] == "TimeoutError"
        assert state["execution_budget"]["llm_calls_used"] == 1
        assert state["execution_budget"]["active_seconds_used"] == 2
        assert service.get_state(state["task_id"]) == state
        assert any(event["event_type"] == "node_failed" for event in service.control.events(state["task_id"]))
    finally:
        service.close()
