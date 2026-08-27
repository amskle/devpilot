import ast
import json
import re
from pathlib import Path

from skills.path_filter import should_scan


JAVA_QUERY_HINTS = ("findBy", "select", "findAll", "listBy", "query", "repository.")
LOOP_KEYWORDS = ("for ", "while ", "stream()", "forEach")


def _python_issues(path: Path) -> list[dict]:
    issues = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = [d for d in node.args.defaults if isinstance(d, (ast.List, ast.Dict, ast.Set))]
            if defaults:
                issues.append(
                    {
                        "issue": "mutable-default-argument",
                        "severity": "Medium",
                        "location": f"{path}:{node.lineno}",
                        "confidence": 0.9,
                    }
                )
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(
                {
                    "issue": "bare-except",
                    "severity": "Medium",
                    "location": f"{path}:{getattr(node, 'lineno', 0)}",
                    "confidence": 0.95,
                }
            )
    return issues


def _java_issues(path: Path) -> list[dict]:
    issues = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for idx, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if any(h in stripped.lower() for h in JAVA_QUERY_HINTS) and any(k in stripped.lower() for k in LOOP_KEYWORDS):
            issues.append(
                {
                    "issue": "n-plus-one-candidate",
                    "severity": "High",
                    "location": f"{path}:{idx}",
                    "confidence": 0.62,
                }
            )
    return issues


def run(inputs: dict) -> dict:
    repo = Path(inputs.get("repo_path", ""))
    language = inputs.get("language", "auto")
    if not repo.exists():
        return {"status": "error", "error": f"repo not found: {repo}"}

    issues = []
    for path in sorted(repo.rglob("*")):
        if not should_scan(repo, path):
            continue
        suffix = path.suffix.lower()
        if language in {"auto", "python"} and suffix == ".py":
            issues.extend(_python_issues(path))
        elif language in {"auto", "java"} and suffix == ".java":
            issues.extend(_java_issues(path))
    return {"status": "ok", "data": {"issues": issues}}


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
