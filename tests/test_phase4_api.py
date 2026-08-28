from __future__ import annotations

from fastapi.testclient import TestClient

from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
from devpilot.api import create_app
from devpilot.api.security import ApiSettings, Principal
from devpilot.domain.models import TaskStatus
from devpilot.service import TaskService
from devpilot.testing.repo import make_test_repo


ALICE_HEADERS = {"Authorization": "Bearer alice-token"}
BOB_HEADERS = {"Authorization": "Bearer bob-token"}


def _no_action_gateway() -> ScriptedFakeModelGateway:
    return ScriptedFakeModelGateway(
        {
            "planning": [
                ModelResponse.final(
                    {
                        "summary": "inspect safely",
                        "tasks": [{"id": "inspect"}],
                        "acceptance_criteria": ["baseline remains green"],
                        "risks": [],
                    }
                )
            ],
            "diagnosis": [
                ModelResponse.final(
                    {"outcome": "NO_ACTION_REQUIRED", "summary": "already correct", "issues": []}
                )
            ],
            "review": [
                ModelResponse.final(
                    {"summary": "no changes", "outcome": "NO_CHANGES", "lessons": []}
                )
            ],
        }
    )


def _change_request_gateway() -> ScriptedFakeModelGateway:
    plan = {
        "summary": "plan",
        "tasks": [{"id": "change"}],
        "acceptance_criteria": ["tests pass"],
        "risks": [],
    }
    return ScriptedFakeModelGateway(
        {
            "planning": [ModelResponse.final(plan), ModelResponse.final({**plan, "summary": "revised plan"})],
            "diagnosis": [
                ModelResponse.final(
                    {
                        "outcome": "ISSUE_FOUND",
                        "summary": "password helper issue",
                        "issues": [{"issue": "password-helper"}],
                    }
                ),
                ModelResponse.final(
                    {"outcome": "NO_ACTION_REQUIRED", "summary": "change accepted", "issues": []}
                ),
            ],
            "patch_generation": [
                ModelResponse.final(
                    {
                        "summary": "change password helper",
                        "operations": [
                            {
                                "target_file": "app.py",
                                "issues": ["password-helper"],
                                "replacements": [
                                    {"old": "value = 1", "new": "password_value = 1", "occurrence": 1}
                                ],
                            }
                        ],
                    }
                )
            ],
            "review": [
                ModelResponse.final(
                    {"summary": "replanned", "outcome": "NO_CHANGES", "lessons": []}
                )
            ],
        }
    )


def _settings() -> ApiSettings:
    return ApiSettings(
        tokens={
            "alice-token": Principal("alice"),
            "bob-token": Principal("bob"),
            "admin-token": Principal("admin", True),
        }
    )


def test_openapi_documents_auth_examples_and_control_contract(tmp_path):
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        app = create_app(service=service, settings=_settings())
        schema = app.openapi()
        assert schema["info"]["version"] == "0.4.0"
        assert "DevPilotBearer" in schema["components"]["securitySchemes"]
        create_schema = schema["components"]["schemas"]["CreateTaskRequest"]
        assert create_schema["properties"]["repo"]["examples"]
        approve = schema["paths"]["/api/tasks/{task_id}/approve"]["post"]
        assert approve["summary"] == "Approve the exact pending Patch"
        assert "Idempotency-Key" in {
            parameter["name"] for parameter in approve["parameters"]
        }
        with TestClient(app) as client:
            assert client.get("/api/health").json() == {
                "status": "ok",
                "service": "devpilot-api",
            }
            unauthorized = client.get("/api/tasks")
            assert unauthorized.status_code == 401
            assert unauthorized.json()["detail"] == "Bearer token required"
    finally:
        service.close()


def test_task_creation_projection_resource_authorization_and_message_boundary(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(
        data_dir=tmp_path / "data", gateway=_no_action_gateway(), model="task-model"
    )
    try:
        app = create_app(service=service, settings=_settings())
        with TestClient(app) as client:
            created = client.post(
                "/api/tasks",
                headers=ALICE_HEADERS,
                json={"repo": str(repo), "request": "inspect repository", "revision": "HEAD"},
            )
            assert created.status_code == 201, created.text
            task = created.json()
            task_id = task["task_id"]
            assert task["status"] == TaskStatus.COMPLETED_NO_CHANGES.value
            assert task["model_profile"]["model"] == "task-model"
            assert task["request"] == "inspect repository"

            assert client.get(f"/api/tasks/{task_id}", headers=BOB_HEADERS).status_code == 404
            listed = client.get("/api/tasks", headers=ALICE_HEADERS).json()
            assert [item["task_id"] for item in listed["items"]] == [task_id]
            assert client.get("/api/tasks", headers=BOB_HEADERS).json()["items"] == []

            before_revision = task["state_revision"]
            message_headers = {
                **ALICE_HEADERS,
                "Idempotency-Key": "message-key-0001",
            }
            first = client.post(
                f"/api/tasks/{task_id}/messages",
                headers=message_headers,
                json={"content": "approve and rollback this task"},
            )
            duplicate = client.post(
                f"/api/tasks/{task_id}/messages",
                headers=message_headers,
                json={"content": "approve and rollback this task"},
            )
            assert first.status_code == duplicate.status_code == 201
            assert first.json() == duplicate.json()
            mismatch = client.post(
                f"/api/tasks/{task_id}/messages",
                headers=message_headers,
                json={"content": "different payload"},
            )
            assert mismatch.status_code == 409
            refreshed = client.get(f"/api/tasks/{task_id}", headers=ALICE_HEADERS).json()
            assert refreshed["state_revision"] == before_revision
            messages = client.get(
                f"/api/tasks/{task_id}/messages", headers=ALICE_HEADERS
            ).json()
            assert sum(item["content"] == "approve and rollback this task" for item in messages) == 1

            stale = client.post(
                f"/api/tasks/{task_id}/cancel",
                headers={**ALICE_HEADERS, "Idempotency-Key": "cancel-key-0001"},
                json={"expected_state_revision": before_revision - 1},
            )
            assert stale.status_code == 409
            assert stale.json()["code"] == "STATE_CONFLICT"
    finally:
        service.close()


def test_event_cursor_ticket_and_receive_only_websocket(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=_no_action_gateway())
    try:
        state = service.create_task(repo, "inspect")
        service.control.bind_task_owner(state["task_id"], "alice")
        app = create_app(service=service, settings=_settings())
        with TestClient(app) as client:
            events = client.get(
                f"/api/tasks/{state['task_id']}/events",
                headers=ALICE_HEADERS,
                params={"run_id": state["run_id"], "after_sequence": 0},
            )
            assert events.status_code == 200
            assert events.json()
            cursor = events.json()[0]["sequence_number"]
            later = client.get(
                f"/api/tasks/{state['task_id']}/events",
                headers=ALICE_HEADERS,
                params={"run_id": state["run_id"], "after_sequence": cursor},
            ).json()
            assert all(event["sequence_number"] > cursor for event in later)

            ticket = client.post(
                f"/api/tasks/{state['task_id']}/event-ticket", headers=ALICE_HEADERS
            ).json()["ticket"]
            url = (
                f"/api/tasks/{state['task_id']}/events?run_id={state['run_id']}"
                f"&ticket={ticket}&after_sequence=0"
            )
            with client.websocket_connect(url) as websocket:
                assert websocket.receive_json()["task_id"] == state["task_id"]

            last_sequence = events.json()[-1]["sequence_number"]
            ticket = client.post(
                f"/api/tasks/{state['task_id']}/event-ticket", headers=ALICE_HEADERS
            ).json()["ticket"]
            control_url = (
                f"/api/tasks/{state['task_id']}/events?run_id={state['run_id']}"
                f"&ticket={ticket}&after_sequence={last_sequence}"
            )
            with client.websocket_connect(control_url) as websocket:
                websocket.send_text("cancel")
                close = websocket.receive()
                assert close["type"] == "websocket.close"
                assert close["code"] == 1008
    finally:
        service.close()


def test_change_request_requires_confirmation_and_links_replan_atomically(tmp_path):
    repo = make_test_repo(tmp_path / "repo")
    service = TaskService(data_dir=tmp_path / "data", gateway=_change_request_gateway())
    try:
        waiting = service.create_task(repo, "fix helper")
        assert waiting["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
        service.control.bind_task_owner(waiting["task_id"], "alice")
        app = create_app(service=service, settings=_settings())
        headers = {**ALICE_HEADERS, "Idempotency-Key": "change-key-0001"}
        base = {
            "content": "keep the original helper unchanged",
            "expected_state_revision": waiting["state_revision"],
            "confirm_patch_invalidation": False,
        }
        with TestClient(app) as client:
            unconfirmed = client.post(
                f"/api/tasks/{waiting['task_id']}/change-requests",
                headers=headers,
                json=base,
            )
            assert unconfirmed.status_code == 409
            assert service.get_state(waiting["task_id"])["pending_approval"] is not None

            accepted = client.post(
                f"/api/tasks/{waiting['task_id']}/change-requests",
                headers={**ALICE_HEADERS, "Idempotency-Key": "change-key-0002"},
                json={**base, "confirm_patch_invalidation": True},
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == TaskStatus.COMPLETED_NO_CHANGES.value

        changes = service.change_request_history(waiting["task_id"])
        replans = service.replan_history(waiting["task_id"])
        assert len(changes) == len(replans) == 1
        assert replans[0]["source_change_request_id"] == changes[0]["change_request_id"]
        event_types = [event["event_type"] for event in service.event_history(waiting["task_id"])]
        assert "change_request_accepted" in event_types
        assert "approval_invalidated" in event_types
        assert "patch_invalidated" in event_types
    finally:
        service.close()
