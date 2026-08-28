from __future__ import annotations

import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from devpilot.agents.definitions import AGENT_SPECS
from devpilot.domain.models import TaskStatus
from devpilot.domain.replay import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationDataset,
    EvaluationMetrics,
    EvaluationReport,
)
from devpilot.events.redaction import sanitize_event_value
from devpilot.services.replay import _digest


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _file_scores(
    expected: list[str], actual: list[str]
) -> tuple[float, float, float]:
    expected_set = set(expected)
    actual_set = set(actual)
    true_positive = len(expected_set & actual_set)
    precision = (
        true_positive / len(actual_set)
        if actual_set
        else float(not expected_set)
    )
    recall = (
        true_positive / len(expected_set)
        if expected_set
        else float(not actual_set)
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return round(precision, 6), round(recall, 6), round(f1, 6)


class EvaluationCommands:
    """Run versioned evaluation datasets and compare immutable reports."""

    def _evaluate_case(
        self,
        case: EvaluationCase,
        *,
        model: str | None,
        prompt_overrides: dict[str, str] | None,
    ) -> EvaluationCaseResult:
        started = time.perf_counter()
        try:
            state = self.create_task(
                Path(case.repo),
                case.request,
                revision=case.revision,
                model=model,
                prompt_overrides=prompt_overrides,
            )
        except Exception as exc:
            return EvaluationCaseResult(
                case_id=case.case_id,
                score=0,
                duration_seconds=max(0, time.perf_counter() - started),
                prompt_tokens=0,
                completion_tokens=0,
                cost="0.0000",
                error=str(sanitize_event_value(str(exc)))[:2_000],
            )

        expectation = case.expectation
        actual_status = str(state["status"])
        expected_statuses = {
            item.value if isinstance(item, TaskStatus) else str(item)
            for item in expectation.statuses
        }
        status_match = actual_status in expected_statuses
        components = [float(status_match)]

        changed_files_precision = None
        changed_files_recall = None
        changed_files_f1 = None
        if expectation.changed_files is not None:
            actual_files = [
                str(item).replace("\\", "/").strip("/")
                for item in (state.get("patch_proposal") or {}).get(
                    "changed_files", []
                )
            ]
            (
                changed_files_precision,
                changed_files_recall,
                changed_files_f1,
            ) = _file_scores(expectation.changed_files, actual_files)
            components.append(changed_files_f1)

        verification_match = None
        if expectation.verification_passed is not None:
            actual_verification = (state.get("verification") or {}).get("passed")
            verification_match = (
                actual_verification is expectation.verification_passed
            )
            components.append(float(verification_match))

        approval_match = None
        if expectation.requires_approval is not None:
            actual_approval = (
                actual_status == TaskStatus.WAITING_RISK_APPROVAL.value
            )
            approval_match = actual_approval is expectation.requires_approval
            components.append(float(approval_match))

        budget = state["execution_budget"]
        return EvaluationCaseResult(
            case_id=case.case_id,
            task_id=state["task_id"],
            run_id=state["run_id"],
            actual_status=actual_status,
            status_match=status_match,
            changed_files_precision=changed_files_precision,
            changed_files_recall=changed_files_recall,
            changed_files_f1=changed_files_f1,
            verification_match=verification_match,
            approval_match=approval_match,
            score=round(sum(components) / len(components), 6),
            duration_seconds=max(0, time.perf_counter() - started),
            prompt_tokens=int(budget.get("prompt_tokens_used", 0)),
            completion_tokens=int(budget.get("completion_tokens_used", 0)),
            cost=str(budget.get("cost_used", "0.0000")),
        )

    def run_evaluation(
        self,
        dataset: EvaluationDataset | dict[str, Any],
        *,
        model: str | None = None,
        prompt_version: str = "default",
        prompt_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        parsed = (
            dataset
            if isinstance(dataset, EvaluationDataset)
            else EvaluationDataset.model_validate(dataset)
        )
        normalized_prompt_version = prompt_version.strip()
        if not normalized_prompt_version:
            raise ValueError("prompt_version must not be empty")
        normalized_overrides = prompt_overrides or {}
        unknown_agents = set(normalized_overrides) - set(AGENT_SPECS)
        if unknown_agents:
            raise ValueError(
                f"unknown prompt override agents: {sorted(unknown_agents)}"
            )
        if any(not value.strip() for value in normalized_overrides.values()):
            raise ValueError("prompt override instructions must not be empty")
        effective_prompts = {
            agent_id: normalized_overrides.get(agent_id, spec.instructions)
            for agent_id, spec in AGENT_SPECS.items()
        }
        selected_model = model or self.model_name
        case_results = [
            self._evaluate_case(
                case,
                model=model,
                prompt_overrides=normalized_overrides,
            )
            for case in parsed.cases
        ]
        completed = [result for result in case_results if result.error is None]
        verification_values = [
            float(result.verification_match)
            for result in case_results
            if result.verification_match is not None
        ]
        approval_values = [
            float(result.approval_match)
            for result in case_results
            if result.approval_match is not None
        ]
        file_values = [
            result.changed_files_f1
            for result in case_results
            if result.changed_files_f1 is not None
        ]
        total_cost = sum(
            (Decimal(result.cost) for result in case_results), Decimal("0")
        )
        metrics = EvaluationMetrics(
            case_count=len(case_results),
            completed_cases=len(completed),
            errored_cases=len(case_results) - len(completed),
            average_score=_average([result.score for result in case_results]) or 0,
            status_accuracy=_average(
                [float(result.status_match) for result in case_results]
            )
            or 0,
            verification_accuracy=_average(verification_values),
            approval_accuracy=_average(approval_values),
            changed_files_f1=_average(
                [float(value) for value in file_values if value is not None]
            ),
            total_prompt_tokens=sum(result.prompt_tokens for result in case_results),
            total_completion_tokens=sum(
                result.completion_tokens for result in case_results
            ),
            total_cost=f"{total_cost:.4f}",
        )
        dataset_dict = parsed.to_state_dict()
        report = EvaluationReport(
            evaluation_id=f"eval_{uuid.uuid4().hex[:16]}",
            dataset_name=parsed.name,
            dataset_version=parsed.version,
            dataset_digest=_digest(dataset_dict),
            model=selected_model,
            prompt_version=normalized_prompt_version,
            prompt_digest=_digest(effective_prompts),
            dataset=parsed,
            metrics=metrics,
            cases=case_results,
            created_at=self.clock.now().isoformat(),
        )
        self.control.save_evaluation_report(report.to_state_dict())
        return report.to_state_dict()

    def evaluation_report(self, evaluation_id: str) -> dict[str, Any]:
        report = self.control.evaluation_report(evaluation_id)
        if report is None:
            raise KeyError(evaluation_id)
        return EvaluationReport.from_state_dict(report).to_state_dict()

    def evaluation_history(self) -> list[dict[str, Any]]:
        return self.control.evaluation_reports()

    def compare_evaluations(
        self,
        baseline_evaluation_id: str,
        candidate_evaluation_id: str,
    ) -> dict[str, Any]:
        baseline = EvaluationReport.from_state_dict(
            self.evaluation_report(baseline_evaluation_id)
        )
        candidate = EvaluationReport.from_state_dict(
            self.evaluation_report(candidate_evaluation_id)
        )
        if baseline.dataset_digest != candidate.dataset_digest:
            raise ValueError("evaluation reports use different datasets")
        deltas = {
            "average_score": round(
                candidate.metrics.average_score - baseline.metrics.average_score,
                6,
            ),
            "status_accuracy": round(
                candidate.metrics.status_accuracy
                - baseline.metrics.status_accuracy,
                6,
            ),
            "total_tokens": float(
                candidate.metrics.total_prompt_tokens
                + candidate.metrics.total_completion_tokens
                - baseline.metrics.total_prompt_tokens
                - baseline.metrics.total_completion_tokens
            ),
            "total_cost": float(
                Decimal(candidate.metrics.total_cost)
                - Decimal(baseline.metrics.total_cost)
            ),
        }
        optional_metrics = (
            "verification_accuracy",
            "approval_accuracy",
            "changed_files_f1",
        )
        for metric_name in optional_metrics:
            baseline_value = getattr(baseline.metrics, metric_name)
            candidate_value = getattr(candidate.metrics, metric_name)
            if baseline_value is not None and candidate_value is not None:
                deltas[metric_name] = round(candidate_value - baseline_value, 6)
        score_delta = deltas["average_score"]
        winner = (
            "CANDIDATE"
            if score_delta > 0
            else "BASELINE" if score_delta < 0 else "TIE"
        )
        return EvaluationComparison(
            baseline_evaluation_id=baseline_evaluation_id,
            candidate_evaluation_id=candidate_evaluation_id,
            metric_deltas=deltas,
            winner=winner,
        ).to_state_dict()
