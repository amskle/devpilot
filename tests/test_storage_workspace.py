import subprocess
from pathlib import Path

import pytest

from devpilot.domain.state import create_initial_state
from devpilot.errors import StateConflictError
from devpilot.services.storage import ArtifactStore, SQLiteControlStore
from devpilot.workspace import WorkspaceManager


def _git(repo: Path, *args: str):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def make_repo(path: Path) -> Path:
    path.mkdir()
    (path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_basic.py").write_text(
        "import unittest\nclass TestBasic(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
        encoding="utf-8",
    )
    _git(path, "init")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "baseline")
    return path


def test_artifacts_are_content_addressed_and_hash_checked(tmp_path):
    store = ArtifactStore(tmp_path / "data")
    first = store.put_text("task", "run", "log", "hello")
    second = store.put_text("task", "run", "log", "hello")
    assert first.sha256 == second.sha256
    assert store.read_text("task", "run", first.to_state_dict()) == "hello"


def test_event_first_projection_uses_optimistic_lock_and_reconcile(tmp_path):
    control = SQLiteControlStore(tmp_path / "control.sqlite")
    state = create_initial_state("task", "run")
    control.create_task(state)
    changed = dict(state)
    changed["status"] = "RUNNING"
    advanced = control.transition(changed, expected_revision=0, event_type="advanced")
    with pytest.raises(StateConflictError):
        control.transition(changed, expected_revision=0, event_type="stale")
    assert control.reconcile(state) is True
    projection = control.get_task("task")
    assert projection["state_revision"] == 0
    assert any(item["event_type"] == "projection_reconciled" for item in control.events("task"))
    control.close()


def test_workspace_rejects_dirty_repo_and_keeps_source_unchanged(tmp_path):
    repo = make_repo(tmp_path / "repo")
    original = (repo / "app.py").read_text(encoding="utf-8")
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")
    manager = WorkspaceManager(tmp_path / "workspaces")
    with pytest.raises(ValueError, match="dirty"):
        manager.create(repo, "task", "run")
    assert (repo / "app.py").read_text(encoding="utf-8") == original


def test_workspace_isolated_clone_and_git_identity(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(repo, "task", "run")
    worktree = Path(workspace.worktree_ref)
    assert worktree != repo
    assert subprocess.run(["git", "-C", str(worktree), "config", "user.name"], capture_output=True, text=True).stdout.strip() == "DevPilot"
    with pytest.raises(Exception):
        manager.resolve_path(workspace, "../escape.txt")
