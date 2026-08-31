import json
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
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


def test_patch_generation_retries_once_with_exact_authorized_source(tmp_path):
    repo = make_repo(tmp_path / "repo")
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {
                        "summary": "plan",
                        "tasks": [],
                        "acceptance_criteria": ["change value"],
                        "risks": [],
                    }
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {
                        "outcome": "ISSUE_FOUND",
                        "summary": "value is stale",
                        "issues": [{"target_file": "app.py", "issue": "stale-value"}],
                    }
                )
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "summary": "first attempt",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["stale-value"],
                                "replacements": [
                                    {"old": "value = 0", "new": "value = 2"}
                                ],
                            }
                        ],
                    }
                ),
                ModelResponse.final(
                    {
                        "summary": "corrected attempt",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["stale-value"],
                                "replacements": [
                                    {"old": "value = 1", "new": "value = 2"}
                                ],
                            }
                        ],
                    }
                ),
            ],
            "review": [
                ModelResponse.final(
                    {
                        "summary": "verified",
                        "outcome": "COMPLETED",
                        "lessons": [],
                    }
                )
            ],
        }
    )
    service = TaskService(data_dir=tmp_path / "data", gateway=gateway)
    try:
        final = service.create_task(repo, "仅修改 app.py：将 value 改为 2")
        assert final["status"] == TaskStatus.COMPLETED.value
        assert gateway.call_count("patch_generation") == 2
        patch_calls = [
            call for call in gateway.calls if call["agent_id"] == "patch_generation"
        ]
        first_context = json.loads(patch_calls[0]["messages"][1]["content"])
        second_context = json.loads(patch_calls[1]["messages"][1]["content"])
        assert first_context["authorized_source_excerpts"]["app.py"] == "value = 1\n"
        assert second_context["runtime_correction"]["code"] == (
            "REPLACEMENT_TARGET_NOT_FOUND"
        )
        assert any(
            event["event_type"] == "patch_proposed"
            and event["payload"]["repair_trigger"]["code"]
            == "REPLACEMENT_TARGET_NOT_FOUND"
            for event in service.control.events(final["task_id"])
        )
    finally:
        service.close()


def test_approval_lease_remains_valid_for_the_full_approval_window(tmp_path):
    repo = make_repo(tmp_path / "repo")
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    service = TaskService(
        data_dir=tmp_path / "data",
        gateway=approval_scenario(),
        clock=clock,
    )
    try:
        waiting = service.create_task(repo, "fix helper")
        clock.advance(seconds=31 * 60)
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


def test_per_task_model_override_selects_gateway_and_survives_resume(tmp_path):
    repo = make_repo(tmp_path / "repo")
    gateway = no_action_scenario()
    selected_models = []

    def gateway_factory(model_name):
        selected_models.append(model_name)
        return gateway

    service = TaskService(
        data_dir=tmp_path / "data",
        gateway_factory=gateway_factory,
        model="default-model",
    )
    try:
        final = service.create_task(repo, "inspect", model="task-model")
        assert selected_models == ["default-model", "task-model"]
        selected_models.clear()

        restored = service.get_state(final["task_id"])
        assert restored == final
        assert selected_models == ["task-model"]
        snapshot = json.loads(
            service.artifacts.read_text(
                final["task_id"],
                final["run_id"],
                {"sha256": final["execution_budget"]["pricing_snapshot_ref"]},
            )
        )
        assert snapshot["selected_model"] == "task-model"
    finally:
        service.close()


def test_injected_gateway_rejects_unenforceable_model_override_before_task_creation(tmp_path):
    repo = make_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data",
        gateway=no_action_scenario(),
        model="fixed-model",
    )
    try:
        try:
            service.create_task(repo, "inspect", model="different-model")
            raise AssertionError("unenforceable model override was accepted")
        except ValueError as exc:
            assert "gateway_factory" in str(exc)
        assert service.control.list_tasks() == []
    finally:
        service.close()


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


def test_pricing_is_recorded_without_a_cost_limit(tmp_path):
    repo = make_repo(tmp_path / "repo")
    data = tmp_path / "data"
    pricing = data / "pricing"
    pricing.mkdir(parents=True)
    (pricing / "catalog.json").write_text(
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
    service = TaskService(data_dir=data, gateway=no_action_scenario(), model="priced")
    try:
        final = service.create_task(repo, "inspect")
        assert final["status"] == TaskStatus.COMPLETED_NO_CHANGES.value
        assert Decimal(final["execution_budget"]["cost_used"]) > 0
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


def test_java_baseline_failure_guides_exact_assertion_fix(tmp_path):
    repo = make_repo(tmp_path / "repo")
    java_test = repo / "src" / "test" / "java" / "com" / "example" / "TestServiceTest.java"
    java_test.parent.mkdir(parents=True)
    java_test.write_text(
        "package com.example;\n"
        "class CalculatorServiceTest {\n"
        "  private TestService calculatorService;\n"
        "  void testDivide() {\n"
        "    assertEquals(0, calculatorService.divide(1, 2));\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    java_service = repo / "src" / "main" / "java" / "com" / "example" / "TestService.java"
    java_service.parent.mkdir(parents=True)
    java_service.write_text(
        "package com.example;\nclass TestService {\n"
        "  double divide(double dividend, double divisor) { return dividend / divisor; }\n}\n",
        encoding="utf-8",
    )
    (repo / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "java division fixture"], check=True, capture_output=True)

    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {"summary": "fix failing assertion", "tasks": [], "acceptance_criteria": ["tests pass"], "risks": []}
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {
                        "outcome": "ISSUE_FOUND",
                        "summary": "Expected 0 is incorrect because divide(1, 2) returns 0.5",
                        "issues": [
                            {
                                "target_file": "src/test/java/com/example/TestServiceTest.java",
                                "old": "assertEquals(0, calculatorService.divide(1, 2));",
                                "new": "assertEquals(0.5, calculatorService.divide(1, 2));",
                            }
                        ],
                    }
                )
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "summary": "correct expected quotient",
                        "operations": [
                            {
                                "target_file": "src/test/java/com/example/TestServiceTest.java",
                                "issues": ["incorrect-assertion"],
                                "replacements": [
                                    {
                                        "old": "assertEquals(0, calculatorService.divide(1, 2));",
                                        "new": "assertEquals(0.5, calculatorService.divide(1, 2));",
                                        "occurrence": 1,
                                    }
                                ],
                            }
                        ],
                    }
                )
            ],
            "review": [
                ModelResponse.final({"summary": "verified", "outcome": "COMPLETED", "lessons": []})
            ],
        }
    )
    check_command = (
        'python -c "from pathlib import Path; import sys; '
        "text=Path('src/test/java/com/example/TestServiceTest.java').read_text(); "
        "fixed='assertEquals(0.5,' in text; "
        "print('TestServiceTest.java:5 expected: <0.0> but was: <0.5>', file=sys.stderr) if not fixed else None; "
        "raise SystemExit(0 if fixed else 1)\""
    )
    service = TaskService(
        data_dir=tmp_path / "data",
        gateway=gateway,
        verification_command=check_command,
    )
    try:
        final = service.create_task(repo, "修复失败测试")
        assert final["status"] == TaskStatus.COMPLETED.value, final
        assert final["verification"]["phase"] == "post_patch"
        assert final["verification"]["passed"] is True
        diagnosis_call = next(call for call in gateway.calls if call["agent_id"] == "diagnosis")
        context = json.loads(diagnosis_call["messages"][1]["content"])
        assert context["baseline_verification"]["phase"] == "baseline"
        assert "expected: <0.0> but was: <0.5>" in context["baseline_verification"]["stderr"]
        workspace_test = Path(final["workspace_ref"]["worktree_ref"]) / java_test.relative_to(repo)
        assert "assertEquals(0.5," in workspace_test.read_text(encoding="utf-8")
        assert "assertEquals(0," in java_test.read_text(encoding="utf-8")
        assert any(
            event["event_type"] == "baseline_verification_executed"
            for event in service.control.events(final["task_id"])
        )
    finally:
        service.close()


def test_failed_baseline_cannot_be_completed_as_no_changes(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "maven fixture"], check=True, capture_output=True)
    gateway = ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final({"summary": "inspect", "tasks": [], "acceptance_criteria": ["tests pass"], "risks": []})
            ],
            "diagnosis": [
                ModelResponse.final({"outcome": "NO_ACTION_REQUIRED", "summary": "no issue", "issues": []})
            ],
        },
        strict=False,
    )
    service = TaskService(
        data_dir=tmp_path / "data",
        gateway=gateway,
        verification_command='python -c "raise SystemExit(1)"',
    )
    try:
        waiting = service.create_task(repo, "修复失败测试")
        assert waiting["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value
        assert waiting["pause_reason"] == "BASELINE_FAILURE_UNDIAGNOSED"
        assert waiting["latest_failure"]["error_code"] == "BASELINE_FAILURE_UNDIAGNOSED"
        assert waiting["review"] is None
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
        gateway=ScriptedFakeModelGateway(
            {"planning": [TimeoutError("Bearer super-secret-credential")]},
            strict=False,
        ),
    )
    try:
        state = service.create_task(repo, "inspect")
        assert state["status"] == TaskStatus.FAILED.value
        assert state["latest_failure"]["category"] == "NODE"
        assert state["latest_failure"]["error_code"] == "TimeoutError"
        assert state["latest_failure"]["summary"] == "Bearer [REDACTED]"
        assert state["execution_budget"]["llm_calls_used"] == 1
        assert state["execution_budget"]["active_seconds_used"] == 2
        assert service.get_state(state["task_id"]) == state
        assert service.cancel(state["task_id"], state["state_revision"]) == state
        assert any(event["event_type"] == "node_failed" for event in service.control.events(state["task_id"]))
    finally:
        service.close()
