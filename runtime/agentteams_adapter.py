from __future__ import annotations

import json

from shared_state.schema import TaskState


def emit_event(task: TaskState, room: str = "devpilot-team") -> dict:
    """Emit a structured AgentTeams-compatible event for later Matrix/API integration."""
    return {
        "room": room,
        "type": "devpilot.task_event",
        "payload": {
            "task_id": task.task_id,
            "status": task.status.value,
            "current_agent": task.current_agent,
            "summary": task.history[-1] if task.history else {},
        },
    }


def emit_json(task: TaskState, room: str = "devpilot-team") -> str:
    return json.dumps(emit_event(task, room), ensure_ascii=False)
