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
MAX_SCANNED_FILE_BYTES = 2 * 1024 * 1024


def should_scan(repo: Path, path: Path) -> bool:
    """Filter repository-relative paths without inspecting hidden parent directories."""
    if path.is_symlink():
        return False
    try:
        resolved_repo = repo.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        relative = resolved_path.relative_to(resolved_repo)
        if not resolved_path.is_file():
            return False
        if resolved_path.stat().st_size > MAX_SCANNED_FILE_BYTES:
            return False
    except (OSError, ValueError):
        return False
    return not any(
        part.startswith(".") or part in IGNORED_DIRECTORIES
        for part in relative.parts
    )
