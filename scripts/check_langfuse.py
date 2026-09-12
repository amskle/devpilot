"""Run a small real DevPilot task against the configured model and Langfuse."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from devpilot.env import load_devpilot_env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", type=Path, default=Path("out/langfuse-smoke"))
    args = parser.parse_args()
    load_devpilot_env(args.env_file, required=True)
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "development")

    # Load tracing integrations only after the environment is available.
    from devpilot import telemetry
    from devpilot.domain.models import ExecutionBudget
    from devpilot.service import TaskService
    from devpilot.testing.repo import make_test_repo

    client = telemetry._client()
    if client is None or not client.auth_check():
        raise SystemExit("Langfuse is unavailable; check the URL, keys and tracing switch in .env.")
    run_dir = args.output_dir.resolve() / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True)
    repo = make_test_repo(run_dir / "source")
    (repo / "requirements.txt").write_text("# This fixture only uses the standard library.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "requirements.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "Declare Python fixture"], check=True, capture_output=True)
    service = TaskService(
        data_dir=run_dir / "data",
        model=os.environ.get("DEVPILOT_MODEL") or "gpt-5-mini",
        base_url=os.environ.get("DEVPILOT_MODEL_BASE_URL") or None,
        verification_command=f'"{sys.executable}" -m unittest discover -s tests',
    )
    try:
        state = service.create_task(
            repo,
            "Langfuse integration smoke check: inspect this intentionally minimal fixture. "
            "The only requirement is that app.py defines value = 1 and the existing tests pass. "
            "If both hold, report NO_ACTION_REQUIRED. Do not add features or strengthen tests.",
            budget=ExecutionBudget(max_llm_calls=8, max_tool_calls=12, max_active_seconds=180),
        )
        report = {
            "task_id": state["task_id"], "run_id": state["run_id"],
            "status": state["status"], "langfuse_session_id": state["task_id"],
            "llm_calls": state["execution_budget"]["llm_calls_used"],
            "report_path": str(run_dir / "result.json"),
        }
        (run_dir / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        if state["status"] not in {"COMPLETED", "COMPLETED_NO_CHANGES"}:
            raise SystemExit("The task did not complete; inspect its Langfuse session and local report.")
    finally:
        service.close()


if __name__ == "__main__":
    main()
