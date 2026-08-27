import json
import re
from pathlib import Path

from skills.path_filter import should_scan


SECRET_RE = re.compile(
    r"(?i)(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]{6,}['\"]"
)
SQL_CONCAT_RE = re.compile(
    r"(?i)((?:select|insert|update|delete)\s+.*\+\s*['\"]|['\"].*\+(?:\s*|\()(?:query|sql))"
)
SQL_PLUS_RE = re.compile(r"(?i)\b(select|insert|update|delete)\b[^;\n]*\+")


def run(inputs: dict) -> dict:
    repo = Path(inputs.get("repo_path", ""))
    if not repo.exists():
        return {"status": "error", "error": f"repo not found: {repo}"}

    issues = []
    for path in sorted(repo.rglob("*")):
        if not should_scan(repo, path):
            continue
        if path.suffix.lower() not in {".py", ".java", ".js", ".ts", ".go", ".properties", ".yml", ".yaml", ".json", ".env"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for idx, line in enumerate(text.splitlines(), 1):
            if SECRET_RE.search(line) and not any(k in line.lower() for k in ("example", "placeholder", "xxxx")):
                issues.append(
                    {
                        "issue": "hardcoded-secret",
                        "severity": "High",
                        "location": f"{path}:{idx}",
                        "confidence": 0.8,
                    }
                )
            if SQL_CONCAT_RE.search(line) or SQL_PLUS_RE.search(line):
                issues.append(
                    {
                        "issue": "sql-injection-candidate",
                        "severity": "High",
                        "location": f"{path}:{idx}",
                        "confidence": 0.7,
                    }
                )
            if re.search(r"\beval\s*\(|\bexec\s*\(|shell\s*=\s*True", line):
                issues.append(
                    {
                        "issue": "dangerous-execution",
                        "severity": "Medium",
                        "location": f"{path}:{idx}",
                        "confidence": 0.75,
                    }
                )
    return {"status": "ok", "data": {"issues": issues}}


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
