from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    ANALYZING = "analyzing"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    MODIFYING = "modifying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskState:
    task_id: str
    repo_path: str
    status: TaskStatus = TaskStatus.ANALYZING
    current_agent: str = "Manager"
    context: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    diffs: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    knowledge: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    approval: dict[str, Any] = field(default_factory=dict)

    def event(self, agent: str, name: str, detail: dict[str, Any] | None = None) -> None:
        self.history.append(
            {
                "event": name,
                "agent": agent,
                "detail": detail or {},
            }
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "task_id": self.task_id,
            "repo_path": self.repo_path,
            "status": self.status.value,
            "current_agent": self.current_agent,
            "context": self.context,
            "issues": self.issues,
            "plan": self.plan,
            "diffs": self.diffs,
            "verification": self.verification,
            "knowledge": self.knowledge,
            "history": self.history,
            "approval": self.approval,
        }
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskState":
        task = cls(
            task_id=data["task_id"],
            repo_path=data["repo_path"],
            status=TaskStatus(data.get("status", TaskStatus.ANALYZING.value)),
            current_agent=data.get("current_agent", "Manager"),
            context=data.get("context", {}),
            issues=data.get("issues", []),
            plan=data.get("plan", {}),
            diffs=data.get("diffs", []),
            verification=data.get("verification", {}),
            knowledge=data.get("knowledge", {}),
            history=data.get("history", []),
            approval=data.get("approval", {}),
        )
        return task

    @classmethod
    def from_json(cls, raw: str) -> "TaskState":
        return cls.from_dict(json.loads(raw))
