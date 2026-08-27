from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from devpilot.clock import Clock, SystemClock
from devpilot.domain.models import WorkspaceRef
from devpilot.errors import PolicyDeniedError


class WorkspaceManager:
    def __init__(self, root: Path, clock: Clock | None = None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()

    @staticmethod
    def _git(repo: Path, *args: str, input_text: str | None = None, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
        return proc.stdout.strip()

    def validate_source(self, source_repo: Path, revision: str = "HEAD") -> str:
        source_repo = source_repo.resolve()
        top = Path(self._git(source_repo, "rev-parse", "--show-toplevel")).resolve()
        if top != source_repo:
            raise ValueError(f"repository path must be Git root: {top}")
        dirty = self._git(source_repo, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise ValueError(f"source repository is dirty:\n{dirty}")
        return self._git(source_repo, "rev-parse", revision)

    def create(self, source_repo: Path, task_id: str, run_id: str, revision: str = "HEAD") -> WorkspaceRef:
        source_repo = source_repo.resolve()
        baseline = self.validate_source(source_repo, revision)
        task_root = self.root / task_id / run_id
        bare = task_root / "repository.git"
        worktree = task_root / "worktree"
        task_root.mkdir(parents=True, exist_ok=False)
        proc = subprocess.run(
            ["git", "clone", "--bare", "--no-local", str(source_repo), str(bare)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "failed to clone task repository")
        self._git(bare, "worktree", "add", "--detach", str(worktree), baseline)
        self._git(worktree, "config", "user.name", "DevPilot")
        self._git(worktree, "config", "user.email", "devpilot@local")
        workspace_id = f"ws_{uuid.uuid4().hex[:16]}"
        return WorkspaceRef(
            workspace_id=workspace_id,
            repository_id=hashlib.sha256(str(source_repo).encode()).hexdigest()[:20],
            worktree_ref=str(worktree),
            baseline_revision=baseline,
            current_revision=baseline,
            lease_owner=run_id,
            lease_expires_at=(self.clock.now() + timedelta(minutes=30)).isoformat(),
        )

    @staticmethod
    def resolve_path(workspace: WorkspaceRef, relative: str) -> Path:
        root = Path(workspace.worktree_ref).resolve()
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise PolicyDeniedError(f"path escapes workspace: {relative}")
        return candidate

    def validate_revision(self, workspace: WorkspaceRef) -> None:
        actual = self._git(Path(workspace.worktree_ref), "rev-parse", "HEAD")
        if actual != workspace.current_revision:
            raise StateConflictError(f"workspace revision changed: expected {workspace.current_revision}, actual {actual}")

    def validate_lease(self, workspace: WorkspaceRef, expected_owner: str | None = None) -> None:
        if expected_owner is not None and workspace.lease_owner != expected_owner:
            raise StateConflictError(
                f"workspace lease owner changed: expected {expected_owner}, actual {workspace.lease_owner}"
            )
        if self.clock.now() >= datetime.fromisoformat(workspace.lease_expires_at):
            raise StateConflictError(f"workspace lease expired: {workspace.workspace_id}")

    def apply_patch(self, workspace: WorkspaceRef, patch: str, expected_hash: str) -> WorkspaceRef:
        actual_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise StateConflictError("patch hash mismatch")
        self.validate_lease(workspace)
        self.validate_revision(workspace)
        root = Path(workspace.worktree_ref)
        tracked_changes = self._git(root, "status", "--porcelain", "--untracked-files=no")
        if tracked_changes:
            raise StateConflictError(f"workspace has unexpected tracked changes:\n{tracked_changes}")
        # Verification may leave untracked bytecode and build outputs. They
        # must not be captured by the next patch commit or shadow new source.
        self._git(root, "clean", "-fdx")
        self._git(root, "apply", "--check", "-", input_text=patch)
        self._git(root, "apply", "-", input_text=patch)
        self._git(root, "add", "-A")
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "DevPilot",
                "GIT_AUTHOR_EMAIL": "devpilot@local",
                "GIT_COMMITTER_NAME": "DevPilot",
                "GIT_COMMITTER_EMAIL": "devpilot@local",
            }
        )
        proc = subprocess.run(
            ["git", "-C", str(root), "commit", "-m", "devpilot: apply approved patch"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "failed to commit patch")
        revision = self._git(root, "rev-parse", "HEAD")
        return workspace.model_copy(update={"current_revision": revision})

    def rollback(self, workspace: WorkspaceRef, revision: str) -> WorkspaceRef:
        root = Path(workspace.worktree_ref).resolve()
        if root != self.root and self.root not in root.parents:
            raise PolicyDeniedError("refusing to clean a workspace outside the DevPilot workspace root")
        self.validate_lease(workspace)
        self.validate_revision(workspace)
        current = self._git(root, "rev-parse", "HEAD")
        if current != revision:
            self._git(root, "reset", "--hard", revision)
        # Verification may leave untracked bytecode/build outputs whose mtime
        # and size can shadow restored source. The target is an already
        # validated, per-task isolated worktree.
        self._git(root, "clean", "-fdx")
        return workspace.model_copy(update={"current_revision": revision})


from devpilot.errors import StateConflictError  # noqa: E402  (keeps exception list close to use)
