from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from devpilot.project_metrics import (  # noqa: E402
    benchmark_artifact_store,
    benchmark_control_store,
    build_metrics_report,
    run_quality_gates,
    write_metrics_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect reproducible architecture, quality, and local benchmark metrics."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "out" / "resume-metrics",
        help="Directory for JSON, Markdown, JUnit, and command logs.",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run backend tests, frontend typecheck, and frontend tests.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run local SQLite/event and artifact-store microbenchmarks.",
    )
    parser.add_argument(
        "--transitions",
        type=int,
        default=1_000,
        help="Control-store transitions used by the local benchmark.",
    )
    parser.add_argument(
        "--artifacts",
        type=int,
        default=100,
        help="Artifact round-trips used by the local benchmark.",
    )
    parser.add_argument(
        "--artifact-bytes",
        type=int,
        default=65_536,
        help="Payload size for each artifact round-trip.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir.resolve()
    quality = None
    if args.run_tests:
        quality = run_quality_gates(ROOT, output_dir / "evidence")
    benchmarks = None
    if args.benchmark:
        benchmarks = {
            "control_store": benchmark_control_store(args.transitions),
            "artifact_store": benchmark_artifact_store(
                args.artifacts,
                args.artifact_bytes,
            ),
        }
    report = build_metrics_report(
        ROOT,
        quality=quality,
        benchmarks=benchmarks,
    )
    json_path, markdown_path = write_metrics_report(report, output_dir)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if quality is not None and not quality["all_passed"]:
        return 1
    if benchmarks is not None and any(
        item["integrity_failures"] for item in benchmarks.values()
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
