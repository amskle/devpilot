from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence

from devpilot.agents.definitions import AGENT_SPECS
from devpilot.domain.models import ExecutionBudget, TaskStatus
from devpilot.domain.state import create_initial_state
from devpilot.services.artifacts import ArtifactStore
from devpilot.services.storage import SQLiteControlStore
from skills.registry import SKILLS


HTTP_METHODS = {"delete", "get", "patch", "post", "put"}


def _source_tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _workflow_node_names(repo_root: Path) -> list[str]:
    tree = _source_tree(repo_root / "devpilot" / "orchestration" / "graph.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "compile_graph":
            continue
        if not node.args or not isinstance(node.args[0], ast.Dict):
            continue
        names = [
            key.value
            for key in node.args[0].keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
        if names:
            return sorted(names)
    raise ValueError("could not find the workflow node registry")


def _api_route_counts(repo_root: Path) -> dict[str, int]:
    counts = {"http": 0, "websocket": 0}
    endpoints = repo_root / "devpilot" / "api" / "v1" / "endpoints"
    for source in endpoints.glob("*.py"):
        tree = _source_tree(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                if (
                    not isinstance(function, ast.Attribute)
                    or not isinstance(function.value, ast.Name)
                    or function.value.id != "router"
                ):
                    continue
                if function.attr in HTTP_METHODS:
                    counts["http"] += 1
                elif function.attr == "websocket":
                    counts["websocket"] += 1
    return counts


def _ci_runtime_versions(repo_root: Path) -> dict[str, list[str]]:
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    python_match = re.search(r'python-version:\s*\[([^]]+)\]', workflow)
    python_versions = (
        re.findall(r'["\']([^"\']+)["\']', python_match.group(1))
        if python_match
        else []
    )
    node_versions = re.findall(r'node-version:\s*["\']?([^"\'\s]+)', workflow)
    return {"python": python_versions, "node": node_versions}


def collect_static_metrics(repo_root: Path) -> dict[str, Any]:
    """Collect version-controlled architecture counts without running services."""

    root = repo_root.resolve()
    routes = _api_route_counts(root)
    budget_limits = sorted(
        name for name in ExecutionBudget.model_fields if name.startswith("max_")
    )
    workflow_nodes = _workflow_node_names(root)
    return {
        "workflow_nodes": {
            "count": len(workflow_nodes),
            "names": workflow_nodes,
        },
        "agents": {
            "count": len(AGENT_SPECS),
            "names": sorted(AGENT_SPECS),
        },
        "skills": {
            "count": len(SKILLS),
            "names": sorted(SKILLS),
        },
        "task_statuses": {
            "count": len(TaskStatus),
            "names": [status.value for status in TaskStatus],
        },
        "execution_budget_limits": {
            "count": len(budget_limits),
            "names": budget_limits,
        },
        "api_routes": {
            **routes,
            "total": routes["http"] + routes["websocket"],
        },
        "ci_runtimes": _ci_runtime_versions(root),
    }


def collect_environment(repo_root: Path) -> dict[str, Any]:
    """Capture enough context to keep local benchmark numbers honest."""

    root = repo_root.resolve()

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    dirty = git("status", "--porcelain", "--untracked-files=all")
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(dirty) if dirty is not None else None,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return ordered[index]


def benchmark_control_store(
    iterations: int = 1_000,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Measure local event/projection transactions and verify their invariants."""

    if iterations < 1:
        raise ValueError("iterations must be positive")

    temporary: TemporaryDirectory[str] | None = None
    if base_dir is None:
        temporary = TemporaryDirectory(prefix="devpilot-metrics-")
        benchmark_root = Path(temporary.name)
    else:
        benchmark_root = base_dir.resolve()
        benchmark_root.mkdir(parents=True, exist_ok=True)

    store = SQLiteControlStore(benchmark_root / "control-benchmark.sqlite")
    try:
        state = create_initial_state("task_metrics", "run_metrics")
        store.create_task(state)
        latencies_ms: list[float] = []
        started = time.perf_counter()
        for index in range(iterations):
            operation_started = time.perf_counter()
            state = store.transition(
                state,
                expected_revision=state["state_revision"],
                event_type="metrics_transition",
                payload={"iteration": index},
            )
            latencies_ms.append(
                (time.perf_counter() - operation_started) * 1_000
            )
        elapsed = time.perf_counter() - started
        store.confirm_checkpoint(
            state["task_id"], state["run_id"], state["state_revision"]
        )
        events = store.event_records(state["task_id"], state["run_id"])
        sequences = [event.sequence_number for event in events]
        expected_sequences = list(range(1, iterations + 2))
        revisions = [
            event.state_revision
            for event in events
            if event.state_revision is not None
        ]
        expected_revisions = list(range(0, iterations + 1))
        unconfirmed_events = sum(
            1
            for event in events
            if event.state_revision is not None and not event.checkpoint_confirmed
        )
        projection = store.get_task(state["task_id"])
        integrity_failures = sum(
            (
                sequences != expected_sequences,
                revisions != expected_revisions,
                projection is None,
                projection is not None
                and projection["state_revision"] != iterations,
                unconfirmed_events != 0,
            )
        )
        return {
            "workload": "sqlite_event_projection_transition",
            "iterations": iterations,
            "event_count": len(events),
            "elapsed_seconds": round(elapsed, 6),
            "operations_per_second": round(iterations / elapsed, 3),
            "latency_ms": {
                "p50": round(_percentile(latencies_ms, 50), 6),
                "p95": round(_percentile(latencies_ms, 95), 6),
                "p99": round(_percentile(latencies_ms, 99), 6),
                "max": round(max(latencies_ms), 6),
            },
            "final_state_revision": state["state_revision"],
            "sequence_gaps": len(set(expected_sequences) - set(sequences)),
            "revision_mismatches": len(
                set(expected_revisions).symmetric_difference(revisions)
            ),
            "unconfirmed_events": unconfirmed_events,
            "integrity_failures": int(integrity_failures),
        }
    finally:
        store.close()
        if temporary is not None:
            temporary.cleanup()


def benchmark_artifact_store(
    artifact_count: int = 100,
    payload_bytes: int = 65_536,
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Measure local immutable artifact round-trips and verify every digest."""

    if artifact_count < 1 or payload_bytes < 32:
        raise ValueError("artifact_count must be positive and payload_bytes >= 32")

    temporary: TemporaryDirectory[str] | None = None
    if base_dir is None:
        temporary = TemporaryDirectory(prefix="devpilot-artifacts-")
        benchmark_root = Path(temporary.name)
    else:
        benchmark_root = base_dir.resolve()
        benchmark_root.mkdir(parents=True, exist_ok=True)

    store = ArtifactStore(benchmark_root / "artifact-benchmark")
    payloads: list[bytes] = []
    refs: list[dict[str, Any]] = []
    write_started = time.perf_counter()
    for index in range(artifact_count):
        prefix = f"artifact-{index:08d}:".encode()
        payload = prefix + bytes([index % 251]) * (payload_bytes - len(prefix))
        payloads.append(payload)
        refs.append(
            store.put_bytes("task_metrics", "run_metrics", "benchmark", payload)
            .to_state_dict()
        )
    write_elapsed = time.perf_counter() - write_started

    failures = 0
    read_started = time.perf_counter()
    for payload, ref in zip(payloads, refs):
        content = store.read_bytes("task_metrics", "run_metrics", ref)
        if content != payload or hashlib.sha256(content).hexdigest() != ref["sha256"]:
            failures += 1
    read_elapsed = time.perf_counter() - read_started
    total_mib = artifact_count * payload_bytes / (1024 * 1024)
    result = {
        "workload": "content_addressed_artifact_roundtrip",
        "artifact_count": artifact_count,
        "payload_bytes": payload_bytes,
        "total_mib": round(total_mib, 6),
        "write_seconds": round(write_elapsed, 6),
        "read_seconds": round(read_elapsed, 6),
        "write_mib_per_second": round(total_mib / write_elapsed, 3),
        "read_mib_per_second": round(total_mib / read_elapsed, 3),
        "integrity_failures": failures,
    }
    if temporary is not None:
        temporary.cleanup()
    return result


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {
        name: sum(int(suite.attrib.get(name, 0)) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    counts["passed"] = (
        counts["tests"]
        - counts["failures"]
        - counts["errors"]
        - counts["skipped"]
    )
    return counts


def _run_gate(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    junit_path: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        elapsed = time.perf_counter() - started
        log_path.write_text(str(exc), encoding="utf-8")
        return {
            "name": name,
            "status": "unavailable",
            "exit_code": None,
            "duration_seconds": round(elapsed, 6),
            "command": command,
            "error": str(exc),
        }

    elapsed = time.perf_counter() - started
    log_path.write_text(
        f"STDOUT\n{result.stdout}\n\nSTDERR\n{result.stderr}", encoding="utf-8"
    )
    gate: dict[str, Any] = {
        "name": name,
        "status": "passed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "duration_seconds": round(elapsed, 6),
        "command": command,
        "log": str(log_path),
    }
    if junit_path is not None and junit_path.is_file():
        gate["tests"] = _junit_counts(junit_path)
        gate["junit"] = str(junit_path)
    return gate


def run_quality_gates(repo_root: Path, evidence_dir: Path) -> dict[str, Any]:
    """Run backend/frontend gates and retain logs plus machine-readable JUnit."""

    root = repo_root.resolve()
    evidence = evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    backend_junit = evidence / "backend-junit.xml"
    frontend_junit = evidence / "frontend-junit.xml"
    npm = shutil.which("npm") or shutil.which("npm.cmd") or "npm"

    gates = [
        _run_gate(
            "backend_tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "skills",
                "tests",
                "-q",
                f"--junitxml={backend_junit}",
            ],
            cwd=root,
            log_path=evidence / "backend-tests.log",
            junit_path=backend_junit,
        ),
        _run_gate(
            "frontend_typecheck",
            [npm, "exec", "vue-tsc", "--", "--noEmit", "--incremental", "false", "-p", "tsconfig.app.json"],
            cwd=root / "frontend" / "vue3",
            log_path=evidence / "frontend-typecheck.log",
        ),
        _run_gate(
            "frontend_tests",
            [
                npm,
                "exec",
                "vitest",
                "--",
                "run",
                "--configLoader",
                "runner",
                "--reporter=junit",
                f"--outputFile={frontend_junit}",
            ],
            cwd=root / "frontend" / "vue3",
            log_path=evidence / "frontend-tests.log",
            junit_path=frontend_junit,
        ),
    ]
    test_counts = [gate.get("tests", {}) for gate in gates]
    return {
        "all_passed": all(gate["status"] == "passed" for gate in gates),
        "passed_tests": sum(int(count.get("passed", 0)) for count in test_counts),
        "skipped_tests": sum(int(count.get("skipped", 0)) for count in test_counts),
        "gates": gates,
    }


def build_metrics_report(
    repo_root: Path,
    *,
    quality: dict[str, Any] | None = None,
    benchmarks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": collect_environment(repo_root),
        "static": collect_static_metrics(repo_root),
        "quality": quality,
        "benchmarks": benchmarks,
    }


def render_metrics_markdown(report: dict[str, Any]) -> str:
    static = report["static"]
    environment = report["environment"]
    lines = [
        "# DevPilot Project Metrics",
        "",
        f"- Collected at: `{environment['collected_at']}`",
        f"- Git commit: `{environment.get('git_commit') or 'unknown'}`",
        f"- Dirty worktree: `{environment.get('git_dirty')}`",
        f"- Platform: `{environment['platform']}`",
        f"- Python: `{environment['python']}`",
        "",
        "## Version-controlled architecture metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| LangGraph workflow nodes | {static['workflow_nodes']['count']} |",
        f"| Agent roles | {static['agents']['count']} |",
        f"| Deterministic skills | {static['skills']['count']} |",
        f"| Execution budget limits | {static['execution_budget_limits']['count']} |",
        f"| REST endpoints | {static['api_routes']['http']} |",
        f"| WebSocket endpoints | {static['api_routes']['websocket']} |",
    ]
    quality = report.get("quality")
    if quality is not None:
        lines.extend(
            [
                "",
                "## Automated quality gates",
                "",
                f"- All gates passed: `{quality['all_passed']}`",
                f"- Tests passed: `{quality['passed_tests']}`",
                f"- Tests skipped: `{quality['skipped_tests']}`",
                "",
                "| Gate | Status | Duration (s) |",
                "|---|---|---:|",
            ]
        )
        lines.extend(
            f"| {gate['name']} | {gate['status']} | {gate['duration_seconds']} |"
            for gate in quality["gates"]
        )
    benchmarks = report.get("benchmarks")
    if benchmarks is not None:
        control = benchmarks["control_store"]
        artifacts = benchmarks["artifact_store"]
        lines.extend(
            [
                "",
                "## Local benchmark observations",
                "",
                "> These figures describe this machine and commit only; they are not production SLAs.",
                "",
                "| Workload | Volume | Throughput | Integrity failures |",
                "|---|---:|---:|---:|",
                (
                    "| SQLite event/projection transitions | "
                    f"{control['iterations']} | {control['operations_per_second']} ops/s | "
                    f"{control['integrity_failures']} |"
                ),
                (
                    "| Content-addressed artifact round-trips | "
                    f"{artifacts['artifact_count']} | {artifacts['write_mib_per_second']} MiB/s write | "
                    f"{artifacts['integrity_failures']} |"
                ),
                "",
                (
                    "Control-store latency: "
                    f"p50 `{control['latency_ms']['p50']} ms`, "
                    f"p95 `{control['latency_ms']['p95']} ms`, "
                    f"p99 `{control['latency_ms']['p99']} ms`."
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Resume-safe interpretation",
            "",
            "Static counts and passed test counts may be quoted with the commit date. "
            "Local throughput and latency must include the workload, machine context, and must not be presented as production capacity.",
            "",
        ]
    )
    return "\n".join(lines)


def write_metrics_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output = output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "project-metrics.json"
    markdown_path = output / "project-metrics.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_metrics_markdown(report), encoding="utf-8")
    return json_path, markdown_path
