import ast
import json
import re
from pathlib import Path

from skills.path_filter import should_scan


JAVA_METHOD_RE = re.compile(
    r"(?ms)^\s*(?:public|private|protected|static|final|synchronized|default|native|abstract|@\w+[\s\S]*?\n\s*)*"
    r"[\w<>,\[\]\s]+\([^)]*\)\s*(?:throws[\s\S]*?)?\{"
)
JAVA_FIELD_RE = re.compile(
    r"(?m)^\s*(?:@\w+(?:\([^\n]*\))?\s*)*"
    r"(?:(?:public|private|protected|static|final|transient|volatile)\s+)*"
    r"(?P<type>[A-Za-z_$][\w$]*(?:\s*<[^;=]+>)?(?:\[\])?)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:=[^;]*)?;"
)
MAX_SOURCE_EVIDENCE_CHARS = 24_000
MAX_SOURCE_EVIDENCE_PER_FILE = 4_000


def _is_test_path(repo: Path, path: Path) -> bool:
    parts = tuple(part.lower() for part in path.relative_to(repo).parts)
    return bool(parts) and (
        parts[0] in {"test", "tests"}
        or any(parts[index : index + 2] == ("src", "test") for index in range(len(parts) - 1))
    )


def _py_file(path: Path) -> dict:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return {"path": str(path), "classes": [], "functions": [], "calls": []}
    classes = []
    functions = []
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        calls.append((node.name, child.func.id))
                    elif isinstance(child.func, ast.Attribute):
                        calls.append((node.name, child.func.attr))
    return {"path": str(path), "classes": classes, "functions": functions, "calls": calls}


def _numbered_excerpt(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(text.splitlines(), 1))
    return numbered[:limit]


def _java_file(path: Path, *, excerpt_limit: int) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    classes = re.findall(r"\b(?:class|interface|enum)\s+(\w+)", text)
    methods = [m.group(0).strip() for m in JAVA_METHOD_RE.finditer(text)]
    calls = re.findall(r"\b(\w+)\.(\w+)\s*\(", text)
    fields = [
        {
            "type": match.group("type").strip(),
            "name": match.group("name"),
            "line": text.count("\n", 0, match.start("name")) + 1,
        }
        for match in JAVA_FIELD_RE.finditer(text)
    ]
    return {
        "path": str(path),
        "classes": classes,
        "functions": [m.split("(", 1)[0].split()[-1] for m in methods[:200]],
        "calls": calls[:500],
        "fields": fields[:200],
        "source_excerpt": _numbered_excerpt(text, excerpt_limit),
    }


def run(inputs: dict) -> dict:
    repo = Path(inputs.get("repo_path", ""))
    language = inputs.get("language", "auto")
    if not repo.exists():
        return {"status": "error", "error": f"repo not found: {repo}"}

    files = []
    classes, functions, call_hints, fields = [], [], [], []
    remaining_source_chars = MAX_SOURCE_EVIDENCE_CHARS
    paths = [path for path in repo.rglob("*") if should_scan(repo, path)]
    paths.sort(
        key=lambda path: (
            0 if _is_test_path(repo, path) else 1,
            path.as_posix().lower(),
        )
    )
    for path in paths:
        suffix = path.suffix.lower()
        if language in {"auto", "python"} and suffix == ".py":
            files.append(_py_file(path))
        elif language in {"auto", "java"} and suffix == ".java":
            excerpt_limit = min(MAX_SOURCE_EVIDENCE_PER_FILE, remaining_source_chars)
            entry = _java_file(path, excerpt_limit=excerpt_limit)
            remaining_source_chars -= len(entry["source_excerpt"])
            files.append(entry)

    for entry in files:
        classes.extend(entry.get("classes", []))
        functions.extend(entry.get("functions", []))
        call_hints.extend(entry.get("calls", []))
        fields.extend({"path": entry["path"], **field} for field in entry.get("fields", []))

    return {
        "status": "ok",
        "data": {
            "files": files,
            "classes": list(dict.fromkeys(classes))[:200],
            "functions": list(dict.fromkeys(functions))[:500],
            "call_hints": call_hints[:1000],
            "fields": fields[:500],
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
