"""Skill registry mapping names to executor modules."""

from pathlib import Path


SKILLS = {
    "project-context": ("project_context", "executor"),
    "code-analysis": ("code_analysis", "executor"),
    "bug-detection": ("bug_detection", "executor"),
    "security-scan": ("security_scan", "executor"),
    "patch-generate": ("patch_generate", "executor"),
    "risk-assessment": ("risk_assessment", "executor"),
    "test-execution": ("test_execution", "executor"),
    "knowledge-extract": ("knowledge_extract", "executor"),
}


def load_skill(name: str):
    package, module = SKILLS[name]
    import importlib

    return importlib.import_module(f"skills.{package}.{module}")


def run_skill(name: str, inputs: dict) -> dict:
    skill = load_skill(name)
    return skill.run(inputs)


def skill_metadata_path(name: str) -> Path:
    package, _ = SKILLS[name]
    return Path(__file__).resolve().parent / package / "metadata.yaml"
