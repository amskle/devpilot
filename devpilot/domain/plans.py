from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from devpilot.domain.models import ArtifactRef, PlanDocument, PlanDraft, ReplanRequest


def _content_hash(draft: PlanDraft) -> str:
    content = {
        "summary": draft.summary,
        "tasks": draft.tasks,
        "acceptance_criteria": draft.acceptance_criteria,
        "risks": draft.risks,
    }
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_plan_document(
    draft: PlanDraft,
    *,
    repository_snapshot_id: str,
    created_at: str,
    previous: PlanDocument | None = None,
    replan_request: ReplanRequest | None = None,
    based_on_diagnosis_revision: int | None = None,
) -> PlanDocument:
    if (previous is None) != (replan_request is None):
        raise ValueError("previous plan and replan request must either both be present or both be absent")
    return PlanDocument(
        plan_id=previous.plan_id if previous else f"plan_{uuid.uuid4().hex[:16]}",
        version=(previous.version + 1) if previous else 1,
        parent_version=previous.version if previous else None,
        created_by="planning",
        created_at=created_at,
        change_reason=replan_request.summary if replan_request else None,
        based_on_diagnosis_revision=based_on_diagnosis_revision if replan_request else None,
        repository_snapshot_id=repository_snapshot_id,
        summary=draft.summary,
        tasks=draft.tasks,
        acceptance_criteria=draft.acceptance_criteria,
        risks=draft.risks,
        content_hash=_content_hash(draft),
    )


def plan_reference(artifact: ArtifactRef, document: PlanDocument) -> dict[str, Any]:
    return {
        **artifact.to_state_dict(),
        "plan_id": document.plan_id,
        "version": document.version,
        "content_hash": document.content_hash,
    }


def create_replan_request(
    *,
    task_id: str,
    run_id: str,
    active_plan_ref: dict[str, Any],
    reason_code: str,
    summary: str,
    requested_at: str,
    evidence_refs: list[str] | None = None,
    source_change_request_id: str | None = None,
    based_on_diagnosis_revision: int | None = None,
) -> ReplanRequest:
    plan_id = active_plan_ref.get("plan_id")
    version = active_plan_ref.get("version")
    if not plan_id or not isinstance(version, int):
        raise ValueError("active plan is not versioned and cannot be replanned safely")
    return ReplanRequest(
        replan_request_id=f"replan_{uuid.uuid4().hex[:16]}",
        task_id=task_id,
        run_id=run_id,
        reason_code=reason_code,
        summary=summary,
        evidence_refs=evidence_refs or [],
        source_change_request_id=source_change_request_id,
        requested_from_plan_id=str(plan_id),
        requested_from_plan_version=version,
        based_on_diagnosis_revision=based_on_diagnosis_revision,
        requested_at=requested_at,
    )
