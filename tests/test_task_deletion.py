from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.api import create_app
from devpilot.api.core.config import ApiSettings, Principal
from devpilot.domain.state import create_initial_state
from devpilot.errors import StateConflictError
from devpilot.service import TaskService
from devpilot.testing.repo import make_test_repo


def _gateway() -> ScriptedFakeModelGateway:
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


def _persist_terminal_task(
    service: TaskService, task_id: str, run_id: str, *, owner: str | None = None
) -> None:
    state = create_initial_state(task_id, run_id)
    state["status"] = "COMPLETED_NO_CHANGES"
    service.control.create_task(state)
    if owner:
        service.control.bind_task_owner(task_id, owner)


def test_delete_task_removes_all_local_history(tmp_path: Path) -> None:
    repo = make_test_repo(tmp_path / "repo")
    data_dir = tmp_path / "data"
    service = TaskService(data_dir=data_dir, gateway=_gateway())
    try:
        state = service.create_task(repo, "inspect repository")
        task_id = state["task_id"]
        run_ids = service.control.task_run_ids(task_id)
        service.control.bind_task_owner(task_id, "alice")
        service.replay_events(task_id)
        service.control.bind_idempotency_input(
            task_id, "test-delete", "request-key", "request-hash"
        )
        service.control.save_idempotent_result(
            task_id, "test-delete", "request-key", {"ok": True}
        )

        assert (data_dir / "tasks" / task_id).is_dir()
        assert (data_dir / "workspaces" / task_id).is_dir()

        assert service.delete_task(task_id) == run_ids

        assert service.control.get_task(task_id) is None
        assert not (data_dir / "tasks" / task_id).exists()
        assert not (data_dir / "workspaces" / task_id).exists()
        for table in (
            "task_projection",
            "execution_events",
            "event_outbox",
            "idempotency_keys",
            "idempotency_inputs",
            "task_owners",
            "plan_documents",
            "plan_lifecycles",
            "replay_records",
        ):
            count = service.control._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            assert count == 0
        for run_id in run_ids:
            assert service._checkpoint_conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (run_id,)
            ).fetchone()[0] == 0
            assert service._checkpoint_conn.execute(
                "SELECT COUNT(*) FROM writes WHERE thread_id=?", (run_id,)
            ).fetchone()[0] == 0
    finally:
        service.close()


def test_delete_task_rejects_non_terminal_state(tmp_path: Path) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_gateway())
    try:
        state = create_initial_state("task_active", "run_active")
        service.control.create_task(state)

        with pytest.raises(StateConflictError, match="only terminal tasks"):
            service.delete_task(state["task_id"])

        assert service.control.get_task(state["task_id"]) is not None
    finally:
        service.close()


def test_bulk_delete_preflights_every_task_before_removing_anything(
    tmp_path: Path,
) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_gateway())
    try:
        _persist_terminal_task(service, "task_one", "run_one")
        _persist_terminal_task(service, "task_two", "run_two")
        active = create_initial_state("task_active", "run_active")
        service.control.create_task(active)

        with pytest.raises(StateConflictError, match="only terminal tasks"):
            service.delete_tasks(["task_one", "task_active"])

        assert service.control.get_task("task_one") is not None
        assert service.delete_tasks(["task_one", "task_two"]) == [
            "task_one",
            "task_two",
        ]
        assert service.control.get_task("task_one") is None
        assert service.control.get_task("task_two") is None
        assert service.control.get_task("task_active") is not None
    finally:
        service.close()


def test_bulk_delete_api_returns_deleted_ids_and_validates_the_batch(
    tmp_path: Path,
) -> None:
    service = TaskService(data_dir=tmp_path / "data", gateway=_gateway())
    try:
        _persist_terminal_task(service, "task_one", "run_one", owner="alice")
        _persist_terminal_task(service, "task_two", "run_two", owner="alice")
        _persist_terminal_task(service, "task_hidden", "run_hidden", owner="bob")
        settings = ApiSettings(tokens={"alice-token": Principal("alice")})
        headers = {"Authorization": "Bearer alice-token"}

        with TestClient(create_app(service=service, settings=settings)) as client:
            duplicate = client.request(
                "DELETE",
                "/api/tasks",
                headers=headers,
                json={"task_ids": ["task_one", "task_one"]},
            )
            assert service.control.get_task("task_one") is not None
            unauthorized_batch = client.request(
                "DELETE",
                "/api/tasks",
                headers=headers,
                json={"task_ids": ["task_one", "task_hidden"]},
            )
            assert service.control.get_task("task_one") is not None
            deleted = client.request(
                "DELETE",
                "/api/tasks",
                headers=headers,
                json={"task_ids": ["task_one", "task_two"]},
            )

        assert duplicate.status_code == 422
        assert unauthorized_batch.status_code == 404
        assert [item["task_id"] for item in service.control.list_tasks()] == [
            "task_hidden"
        ]
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted_task_ids": ["task_one", "task_two"]}
    finally:
        service.close()


def test_delete_task_api_enforces_owner_and_returns_no_content(tmp_path: Path) -> None:
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=_gateway())
    try:
        state = service.create_task(repo, "inspect repository")
        service.control.bind_task_owner(state["task_id"], "alice")
        active = create_initial_state("task_active_api", "run_active_api")
        service.control.create_task(active)
        service.control.bind_task_owner(active["task_id"], "alice")
        settings = ApiSettings(
            tokens={
                "alice-token": Principal("alice"),
                "bob-token": Principal("bob"),
            }
        )
        with TestClient(create_app(service=service, settings=settings)) as client:
            hidden = client.delete(
                f"/api/tasks/{state['task_id']}",
                headers={"Authorization": "Bearer bob-token"},
            )
            active_denied = client.delete(
                f"/api/tasks/{active['task_id']}",
                headers={"Authorization": "Bearer alice-token"},
            )
            deleted = client.delete(
                f"/api/tasks/{state['task_id']}",
                headers={"Authorization": "Bearer alice-token"},
            )
            missing = client.get(
                f"/api/tasks/{state['task_id']}",
                headers={"Authorization": "Bearer alice-token"},
            )

        assert hidden.status_code == 404
        assert active_denied.status_code == 409
        assert active_denied.json()["code"] == "STATE_CONFLICT"
        assert service.control.get_task(active["task_id"]) is not None
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert missing.status_code == 404
    finally:
        service.close()
