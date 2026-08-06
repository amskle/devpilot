import ast
import json
import re
from pathlib import Path


JAVA_METHOD_RE = re.compile(
    r"(?ms)^\s*(?:public|private|protected|static|final|synchronized|default|native|abstract|@\w+[\s\S]*?\n\s*)*"
    r"[\w<>,\[\]\s]+\([^)]*\)\s*(?:throws[\s\S]*?)?\{"
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


def _java_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    classes = re.findall(r"\b(?:class|interface|enum)\s+(\w+)", text)
    methods = [m.group(0).strip() for m in JAVA_METHOD_RE.finditer(text)]
    calls = re.findall(r"\b(\w+)\.(\w+)\s*\(", text)
    return {
        "path": str(path),
        "classes": classes,
        "functions": [m.split("(", 1)[0].split()[-1] for m in methods[:200]],
        "calls": calls[:500],
    }


def run(inputs: dict) -> dict:
    repo = Path(inputs.get("repo_path", ""))
    language = inputs.get("language", "auto")
    if not repo.exists():
        return {"status": "error", "error": f"repo not found: {repo}"}

    files = []
    classes, functions, call_hints = [], [], []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.parts):
            continue
        suffix = path.suffix.lower()
        if language in {"auto", "python"} and suffix == ".py":
            files.append(_py_file(path))
        elif language in {"auto", "java"} and suffix == ".java":
            files.append(_java_file(path))

    for entry in files:
        classes.extend(entry.get("classes", []))
        functions.extend(entry.get("functions", []))
        call_hints.extend(entry.get("calls", []))

    return {
        "status": "ok",
        "data": {
            "files": files,
            "classes": list(dict.fromkeys(classes))[:200],
            "functions": list(dict.fromkeys(functions))[:500],
            "call_hints": call_hints[:1000],
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
