from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from devpilot.domain.models import ModelProfile, RecoveryPoint, TaskStatus


class TaskQueries:
    """Provide read models, histories, traces, and task conversation messages."""

    def task_view(self, task_id: str) -> dict[str, Any]:
        state = self.get_state(task_id)
        projection = self.control.get_task(task_id)
        if projection is None:  # pragma: no cover - guarded by get_state
            raise KeyError(task_id)
        _, selected_model = self._pricing_context(state)
        return {
            **copy.deepcopy(state),
            "request": self._request_from_state(state),
            "updated_at": projection["updated_at"],
            "model_profile": ModelProfile(
                provider="openai-compatible", model=selected_model
            ).to_state_dict(),
        }

    def list_task_views(self, *, status: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for projection in self.control.list_tasks():
            state = projection["state"]
            if state["status"] == TaskStatus.WAITING_RISK_APPROVAL.value:
                state = self.get_state(state["task_id"])
                refreshed = self.control.get_task(state["task_id"])
                if refreshed is not None:
                    projection = refreshed
            if status and state["status"] != status:
                continue
            try:
                request = self._request_from_state(state)
                _, selected_model = self._pricing_context(state)
            except (FileNotFoundError, ValueError):
                request = ""
                selected_model = self.model_name
            result.append(
                {
                    "task_id": state["task_id"],
                    "run_id": state["run_id"],
                    "status": state["status"],
                    "current_node": state["current_node"],
                    "state_revision": state["state_revision"],
                    "pause_reason": state["pause_reason"],
                    "request": request,
                    "model": selected_model,
                    "updated_at": projection["updated_at"],
                    "execution_budget": state["execution_budget"],
                    "verification": state["verification"],
                }
            )
        return result

    def plan_documents(self, task_id: str) -> list[dict[str, Any]]:
        if self.control.get_task(task_id) is None:
            raise KeyError(task_id)
        return [
            {**item["document"], **item["lifecycle"]}
            for item in self.plan_history(task_id)
        ]

    def diff_document(self, task_id: str) -> dict[str, Any]:
        state = self.get_state(task_id)
        proposal = state.get("patch_proposal")
        if proposal is None:
            raise KeyError("diff")
        return {
            "patch_id": proposal.get("patch_id"),
            "text": self.artifacts.read_text(
                task_id, state["run_id"], proposal["patch_ref"]
            ),
            "changed_files": proposal.get("changed_files", []),
            "patch_hash": proposal.get("patch_hash"),
        }

    def recovery_points(self, task_id: str) -> list[dict[str, Any]]:
        state = self.get_state(task_id)
        reference = state.get("active_recovery_point_ref")
        if not reference:
            return []
        raw = self.artifacts.read_text(
            task_id, state["run_id"], {"sha256": reference}
        )
        return [RecoveryPoint.from_state_dict(json.loads(raw)).to_state_dict()]

    def add_message(
        self,
        task_id: str,
        content: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        operation = "messages"
        if idempotency_key:
            cached = self.control.idempotent_result(
                task_id, operation, idempotency_key
            )
            if cached:
                return cached
        state = self.get_state(task_id)
        normalized = content.strip()
        if not normalized:
            raise ValueError("message content must not be empty")
        message = {
            "message_id": f"message_{uuid.uuid4().hex[:16]}",
            "role": "user",
            "content": normalized,
            "created_at": self.clock.now().isoformat(),
        }
        event = self.control.append_event(
            task_id,
            state["run_id"],
            "message_created",
            message,
            correlation_id=message["message_id"],
        )
        message = dict(event.payload)
        if idempotency_key:
            self.control.save_idempotent_result(
                task_id, operation, idempotency_key, message
            )
        return message

    def messages(self, task_id: str) -> list[dict[str, Any]]:
        state = self.get_state(task_id)
        request = self._request_from_state(state)
        events = self.control.event_records(task_id)
        created_event = next(
            (event for event in events if event.event_type == "task_created"), None
        )
        result: list[dict[str, Any]] = []
        if request:
            result.append(
                {
                    "message_id": f"message_{task_id}_request",
                    "role": "user",
                    "content": request,
                    "created_at": (
                        created_event.created_at
                        if created_event is not None
                        else self.clock.now().isoformat()
                    ),
                }
            )
        for event in events:
            if event.event_type == "message_created":
                result.append(dict(event.payload))
                continue
            summary = event.payload.get("agent_summary")
            if isinstance(summary, str) and summary.strip():
                result.append(
                    {
                        "message_id": f"message_{event.event_id}",
                        "role": "assistant",
                        "content": summary,
                        "created_at": event.created_at,
                    }
                )
        return result

    def plan_history(self, task_id: str) -> list[dict[str, Any]]:
        return self.control.plans(task_id)

    def replan_history(self, task_id: str) -> list[dict[str, Any]]:
        return self.control.replan_requests(task_id)

    def change_request_history(self, task_id: str) -> list[dict[str, Any]]:
        return self.control.change_requests(task_id)

    def event_history(
        self,
        task_id: str,
        run_id: str | None = None,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if self.control.get_task(task_id) is None:
            raise KeyError(task_id)
        return self.control.events(
            task_id, run_id, after_sequence=after_sequence, limit=limit
        )

    def trace(self, task_id: str, run_id: str | None = None) -> dict[str, Any]:
        if self.control.get_task(task_id) is None:
            raise KeyError(task_id)
        return self.control.trace(task_id, run_id)
