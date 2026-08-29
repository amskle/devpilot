import json
from pathlib import Path


BUILD_MARKERS = {
    "pom.xml": ("java", "maven"),
    "build.gradle": ("java", "gradle"),
    "build.gradle.kts": ("java", "gradle"),
    "package.json": ("javascript", "npm"),
    "pyproject.toml": ("python", "pip"),
    "requirements.txt": ("python", "pip"),
    "go.mod": ("go", "go"),
    "Cargo.toml": ("rust", "cargo"),
}


def _safe_read(path: Path, limit: int = 4096) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def run(inputs: dict) -> dict:
    repo = Path(inputs.get("repo_path", ""))
    if not repo.exists():
        return {"status": "error", "error": f"repo not found: {repo}"}

    entries = sorted([p for p in repo.iterdir() if not p.name.startswith(".")])
    files = [p for p in entries if p.is_file()]
    dirs = [p for p in entries if p.is_dir()]

    project_type = "unknown"
    build_tool = "unknown"
    for marker, (ptype, tool) in BUILD_MARKERS.items():
        if (repo / marker).exists():
            project_type, build_tool = ptype, tool
            if marker == "pyproject.toml" and "[tool.poetry]" in _safe_read(
                repo / marker
            ):
                build_tool = "poetry"
            break

    tech_stack = [project_type]
    if (repo / "pom.xml").exists():
        pom = _safe_read(repo / "pom.xml")
        for dep in ("spring-boot", "mybatis", "mysql", "redis", "dubbo"):
            if dep in pom.lower():
                tech_stack.append(dep)

    configs = [
        p.name
        for p in files
        if p.suffix.lower() in {".yml", ".yaml", ".xml", ".toml", ".json", ".properties", ".ini"}
    ]
    structure = [p.name for p in dirs[:30]]

    return {
        "status": "ok",
        "data": {
            "project_type": project_type,
            "tech_stack": tech_stack,
            "build_tool": build_tool,
            "structure": structure,
            "configs": configs,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
