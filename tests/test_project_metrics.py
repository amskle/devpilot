from __future__ import annotations

import json
from pathlib import Path

from devpilot.project_metrics import (
    benchmark_artifact_store,
    benchmark_control_store,
    build_metrics_report,
    collect_static_metrics,
    render_metrics_markdown,
    write_metrics_report,
)


ROOT = Path(__file__).resolve().parents[1]


def test_static_metrics_match_version_controlled_contracts():
    metrics = collect_static_metrics(ROOT)

    assert metrics["workflow_nodes"]["count"] == 15
    assert metrics["agents"]["count"] == 4
    assert metrics["skills"]["count"] == 8
    assert metrics["task_statuses"]["count"] == 10
    assert metrics["execution_budget_limits"]["count"] == 9
    assert metrics["api_routes"] == {"http": 19, "websocket": 1, "total": 20}
    assert metrics["ci_runtimes"] == {
        "python": ["3.10", "3.13"],
        "node": ["22"],
    }


def test_local_benchmarks_verify_sequences_revisions_and_artifact_hashes(tmp_path):
    control = benchmark_control_store(12, base_dir=tmp_path / "control")
    artifacts = benchmark_artifact_store(
        4,
        1_024,
        base_dir=tmp_path / "artifacts",
    )

    assert control["iterations"] == 12
    assert control["event_count"] == 13
    assert control["final_state_revision"] == 12
    assert control["sequence_gaps"] == 0
    assert control["revision_mismatches"] == 0
    assert control["unconfirmed_events"] == 0
    assert control["integrity_failures"] == 0
    assert control["operations_per_second"] > 0
    assert control["latency_ms"]["p95"] > 0
    assert artifacts["artifact_count"] == 4
    assert artifacts["integrity_failures"] == 0
    assert artifacts["write_mib_per_second"] > 0
    assert artifacts["read_mib_per_second"] > 0


def test_report_writes_machine_and_human_readable_evidence(tmp_path):
    report = build_metrics_report(ROOT)
    json_path, markdown_path = write_metrics_report(report, tmp_path / "report")

    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert persisted["schema_version"] == 1
    assert persisted["static"]["workflow_nodes"]["count"] == 15
    assert "Version-controlled architecture metrics" in markdown
    assert "not production SLAs" not in markdown
    assert render_metrics_markdown(report) == markdown
