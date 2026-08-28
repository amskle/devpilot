from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from devpilot.domain.models import ExecutionBudget, RecoveryPoint, WorkspaceRef
from devpilot.domain.replay import (
    EventReplayResult,
    RecoveryForkResult,
    ReplayIssue,
    StateReplayResult,
)
from devpilot.domain.state import GraphStateSchema, validate_state


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ReplayCommands:
    """Side-effect-free event/state replay and isolated recovery forks."""

    def _event_replay(
        self,
        task_id: str,
        run_id: str,
        *,
        through_sequence: int | None,
        persist: bool,
    ) -> EventReplayResult:
        projection = self.control.get_task(task_id)
        if projection is None:
            raise KeyError(task_id)
        events = self.control.event_records(task_id, run_id)
        if not events:
            raise KeyError(f"event run not found: {run_id}")
        if through_sequence is not None:
            if through_sequence < 1:
                raise ValueError("through_sequence must be positive")
            events = [
                event
                for event in events
                if event.sequence_number <= through_sequence
            ]
            if not events:
                raise ValueError("through_sequence precedes the first event")

        issues: list[ReplayIssue] = []
        seen_ids: set[str] = set()
        previous_revision: int | None = None
        expected_sequence = 1
        for event in events:
            if event.sequence_number != expected_sequence:
                issues.append(
                    ReplayIssue(
                        severity="ERROR",
                        code="SEQUENCE_GAP",
                        detail=(
                            f"expected sequence {expected_sequence}, "
                            f"found {event.sequence_number}"
                        ),
                        sequence_number=event.sequence_number,
                    )
                )
                expected_sequence = event.sequence_number
            expected_sequence += 1
            if event.causation_id is not None and event.causation_id not in seen_ids:
                issues.append(
                    ReplayIssue(
                        severity="ERROR",
                        code="DANGLING_CAUSATION",
                        detail=f"causation event is not earlier in this run: {event.causation_id}",
                        sequence_number=event.sequence_number,
                    )
                )
            if event.state_revision is not None:
                if (
                    previous_revision is not None
                    and event.state_revision < previous_revision
                ):
                    issues.append(
                        ReplayIssue(
                            severity="ERROR",
                            code="STATE_REVISION_REGRESSION",
                            detail=(
                                f"state revision moved from {previous_revision} "
                                f"to {event.state_revision}"
                            ),
                            sequence_number=event.sequence_number,
                        )
                    )
                previous_revision = event.state_revision
                if not event.checkpoint_confirmed:
                    issues.append(
                        ReplayIssue(
                            severity="WARNING",
                            code="UNCONFIRMED_STATE_EVENT",
                            detail=(
                                f"state revision {event.state_revision} has no "
                                "confirmed checkpoint"
                            ),
                            sequence_number=event.sequence_number,
                        )
                    )
            seen_ids.add(event.event_id)

        event_dicts = [event.to_state_dict() for event in events]
        replay = EventReplayResult(
            replay_id=f"replay_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            run_id=run_id,
            through_sequence=events[-1].sequence_number,
            event_count=len(events),
            first_sequence=events[0].sequence_number,
            last_sequence=events[-1].sequence_number,
            last_state_revision=previous_revision,
            event_type_counts=dict(Counter(event.event_type for event in events)),
            source_digest=_digest(event_dicts),
            integrity_ok=not any(issue.severity == "ERROR" for issue in issues),
            issues=issues,
            created_at=self.clock.now().isoformat(),
        )
        if persist:
            self.control.save_replay_record(
                replay.replay_id,
                task_id,
                run_id,
                replay.replay_type,
                replay.source_digest,
                replay.to_state_dict(),
            )
        return replay

    def replay_events(
        self,
        task_id: str,
        run_id: str | None = None,
        *,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        projection = self.control.get_task(task_id)
        if projection is None:
            raise KeyError(task_id)
        selected_run = run_id or str(projection["run_id"])
        return self._event_replay(
            task_id,
            selected_run,
            through_sequence=through_sequence,
            persist=True,
        ).to_state_dict()

    def replay_state(
        self,
        task_id: str,
        run_id: str | None = None,
        *,
        state_revision: int | None = None,
    ) -> dict[str, Any]:
        projection = self.control.get_task(task_id)
        if projection is None:
            raise KeyError(task_id)
        selected_run = run_id or str(projection["checkpoint_run_id"])
        checkpoints = list(self.checkpointer.list(self._config(selected_run)))
        if not checkpoints:
            raise KeyError(f"checkpoint run not found: {selected_run}")

        selected = None
        selected_state = None
        state_fields = set(GraphStateSchema.model_fields)
        for checkpoint in checkpoints:
            values = checkpoint.checkpoint.get("channel_values", {})
            try:
                candidate = validate_state(
                    {key: value for key, value in values.items() if key in state_fields}
                )
            except ValueError:
                continue
            if state_revision is None or candidate["state_revision"] == state_revision:
                selected = checkpoint
                selected_state = candidate
                break
        if selected is None or selected_state is None:
            raise KeyError(
                f"state revision not found in run {selected_run}: {state_revision}"
            )

        revision = selected_state["state_revision"]
        records = self.control.event_records(task_id, selected_run)
        state_events = [
            event
            for event in records
            if event.state_revision is not None and event.state_revision <= revision
        ]
        boundary = max(
            (event.sequence_number for event in state_events),
            default=records[0].sequence_number if records else 1,
        )
        event_replay = self._event_replay(
            task_id,
            selected_run,
            through_sequence=boundary,
            persist=False,
        )
        issues = list(event_replay.issues)
        confirmed_revisions = [
            event.state_revision
            for event in state_events
            if event.checkpoint_confirmed and event.state_revision is not None
        ]
        latest_confirmed = max(confirmed_revisions, default=None)
        if selected_state["task_id"] != task_id or selected_state["run_id"] != selected_run:
            issues.append(
                ReplayIssue(
                    severity="ERROR",
                    code="CHECKPOINT_IDENTITY_MISMATCH",
                    detail="checkpoint task_id or run_id does not match the replay target",
                )
            )
        if latest_confirmed != revision:
            issues.append(
                ReplayIssue(
                    severity="ERROR",
                    code="CHECKPOINT_EVENT_REVISION_MISMATCH",
                    detail=(
                        f"checkpoint revision is {revision}, latest confirmed "
                        f"event revision is {latest_confirmed}"
                    ),
                )
            )

        checkpoint_id = str(selected.config["configurable"]["checkpoint_id"])
        parent_checkpoint_id = None
        if selected.parent_config is not None:
            parent_checkpoint_id = str(
                selected.parent_config["configurable"]["checkpoint_id"]
            )
        replay = StateReplayResult(
            replay_id=f"replay_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            run_id=selected_run,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            checkpoint_count=len(checkpoints),
            state_revision=revision,
            state_digest=_digest(selected_state),
            event_digest=event_replay.source_digest,
            consistent=not any(issue.severity == "ERROR" for issue in issues),
            issues=issues,
            state=selected_state,
            created_at=self.clock.now().isoformat(),
        )
        self.control.save_replay_record(
            replay.replay_id,
            task_id,
            selected_run,
            replay.replay_type,
            replay.state_digest,
            replay.to_state_dict(),
        )
        return replay.to_state_dict()

    def replay_history(self, task_id: str) -> list[dict[str, Any]]:
        if self.control.get_task(task_id) is None:
            raise KeyError(task_id)
        return self.control.replay_records(task_id)

    def fork_recovery_point(
        self,
        task_id: str,
        recovery_point_id: str,
        *,
        model: str | None = None,
        budget: ExecutionBudget | None = None,
    ) -> dict[str, Any]:
        source = self.get_state(task_id)
        reference = source.get("active_recovery_point_ref")
        if not reference:
            raise ValueError("task has no active recovery point")
        raw = self.artifacts.read_text(
            task_id, source["run_id"], {"sha256": reference}
        )
        recovery = RecoveryPoint.from_state_dict(json.loads(raw))
        if recovery.recovery_point_id != recovery_point_id:
            raise ValueError("recovery point does not match active recovery point")

        workspace = WorkspaceRef.from_state_dict(source["workspace_ref"] or {})
        bare_repository = (Path(workspace.worktree_ref).parent / "repository.git").resolve()
        if (
            bare_repository != self.workspace_manager.root
            and self.workspace_manager.root not in bare_repository.parents
        ):
            raise ValueError("recovery source repository escaped the workspace root")
        request = self._request_from_state(source)
        target = self.create_task(
            bare_repository,
            request,
            revision=recovery.repository_snapshot_id,
            budget=budget,
            model=model,
            parent_run_id=source["run_id"],
        )
        result = RecoveryForkResult(
            fork_id=f"fork_{uuid.uuid4().hex[:16]}",
            source_task_id=task_id,
            source_run_id=source["run_id"],
            recovery_point_id=recovery.recovery_point_id,
            repository_snapshot_id=recovery.repository_snapshot_id,
            target_task_id=target["task_id"],
            target_run_id=target["run_id"],
            model=model or self.model_name,
            created_at=self.clock.now().isoformat(),
        )
        self.control.save_recovery_fork(result.to_state_dict())
        return result.to_state_dict()
