from __future__ import annotations

from pathlib import Path


IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}


def should_scan(repo: Path, path: Path) -> bool:
    """Filter repository-relative paths without inspecting hidden parent directories."""
    if not path.is_file():
        return False
    try:
        relative = path.relative_to(repo)
    except ValueError:
        return False
    return not any(
        part.startswith(".") or part in IGNORED_DIRECTORIES
        for part in relative.parts
    )
