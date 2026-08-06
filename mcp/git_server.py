import json
import logging
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("devpilot-git")
LOGGER = logging.getLogger("devpilot.git")


def _run(repo: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(Path(repo)),
        capture_output=True,
        text=True,
        check=check,
    )
    return proc


@mcp.tool()
def repo_status(repo_path: str) -> str:
    """Return git status porcelain output for a repository."""
    proc = _run(repo_path, "status", "--porcelain")
    return proc.stdout or proc.stderr


@mcp.tool()
def list_branches(repo_path: str) -> str:
    """List local branches in a repository."""
    proc = _run(repo_path, "branch", "--list")
    return proc.stdout or proc.stderr


@mcp.tool()
def create_snapshot(repo_path: str, snapshot_branch: str) -> str:
    """Create a lightweight snapshot branch before applying changes."""
    proc = _run(repo_path, "branch", snapshot_branch)
    if proc.returncode != 0:
        return proc.stderr.strip() or "failed"
    return f"snapshot branch created: {snapshot_branch}"


@mcp.tool()
def get_diff(repo_path: str, base: str = "HEAD") -> str:
    """Return the current working-tree diff against base (default HEAD)."""
    proc = _run(repo_path, "diff", base)
    return proc.stdout or proc.stderr


@mcp.tool()
def apply_patch(repo_path: str, patch_text: str, apply: bool = False) -> str:
    """Check a patch with git apply --check; only apply when apply=true."""
    check = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=str(Path(repo_path)),
        input=patch_text,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return f"patch check failed: {check.stderr.strip()}"
    if not apply:
        return "patch check passed (not applied)"
    applied = subprocess.run(
        ["git", "apply", "-"],
        cwd=str(Path(repo_path)),
        input=patch_text,
        capture_output=True,
        text=True,
    )
    return "patch applied" if applied.returncode == 0 else f"patch apply failed: {applied.stderr.strip()}"


@mcp.tool()
def rollback(repo_path: str, snapshot_branch: str, confirm: str = "no") -> str:
    """Roll back to snapshot_branch. Requires confirm='yes'."""
    if confirm != "yes":
        return "rollback requires confirm='yes'"
    proc = _run(repo_path, "reset", "--hard", snapshot_branch)
    return "rollback completed" if proc.returncode == 0 else proc.stderr.strip()


@mcp.tool()
def audit_log(repo_path: str, tail: int = 20) -> str:
    """Return recent git log entries for audit evidence."""
    proc = _run(repo_path, "log", "-n", str(tail), "--pretty=format:%h %an %ad %s", "--date=iso")
    return proc.stdout or proc.stderr


if __name__ == "__main__":
    mcp.run()
