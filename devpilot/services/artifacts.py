from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from devpilot.domain.models import ArtifactRef


class ArtifactStore:
    """Content-addressed storage for immutable task artifacts."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self, task_id: str, run_id: str, kind: str, content: bytes
    ) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"art_{digest[:20]}"
        directory = self.root / "tasks" / task_id / "runs" / run_id / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / digest
        if not target.exists():
            temporary = directory / f".{digest}.{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(content)
            os.replace(temporary, target)
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            sha256=digest,
            size=len(content),
        )

    def put_text(
        self, task_id: str, run_id: str, kind: str, content: str
    ) -> ArtifactRef:
        return self.put_bytes(task_id, run_id, kind, content.encode("utf-8"))

    def put_json(
        self, task_id: str, run_id: str, kind: str, value: Any
    ) -> ArtifactRef:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return self.put_text(task_id, run_id, kind, raw)

    def read_bytes(
        self, task_id: str, run_id: str, ref: dict[str, Any]
    ) -> bytes:
        target = (
            self.root
            / "tasks"
            / task_id
            / "runs"
            / run_id
            / "artifacts"
            / ref["sha256"]
        ).resolve()
        if self.root not in target.parents:
            raise ValueError("artifact path escaped store root")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != ref["sha256"]:
            raise ValueError("artifact hash mismatch")
        return content

    def read_text(self, task_id: str, run_id: str, ref: dict[str, Any]) -> str:
        return self.read_bytes(task_id, run_id, ref).decode("utf-8")
