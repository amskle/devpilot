from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from devpilot.api.core.dependencies import AuthenticatedPrincipal, ControlPlaneDependency
from devpilot.api.schemas import ERROR_RESPONSES
from devpilot.api.schemas.repositories import DirectoryEntry, RepositoryDirectoryResponse
from devpilot.errors import PolicyDeniedError


router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get(
    "/directories",
    response_model=RepositoryDirectoryResponse,
    summary="Browse permitted repository directories",
    responses=ERROR_RESPONSES,
    operation_id="listRepositoryDirectories",
)
def list_directories(
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    path: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
) -> RepositoryDirectoryResponse:
    if not principal.is_admin and not principal.can_create_tasks:
        raise PolicyDeniedError("task creation requires admin or task_creator permission")
    if not control.repository_roots and not principal.is_admin:
        raise PolicyDeniedError("repository browsing requires DEVPILOT_API_REPOSITORY_ROOTS")

    try:
        if path is None:
            roots = control.repository_roots
            if not roots:
                roots = (
                    tuple(Path(f"{drive}:/") for drive in string.ascii_uppercase
                          if Path(f"{drive}:/").is_dir())
                    if os.name == "nt" else (Path("/"),)
                )
            return RepositoryDirectoryResponse(
                path=None, parent=None,
                items=[DirectoryEntry(name=root.name or str(root), path=str(root))
                       for root in roots],
            )

        directory = control._authorize_repository(Path(path), principal)
        parent = None
        if directory.parent != directory:
            try:
                parent = str(control._authorize_repository(directory.parent, principal))
            except PolicyDeniedError:
                pass
        entries = []
        for child in directory.iterdir():
            # Resolve before returning any path; links cannot escape the allowed roots.
            try:
                if child.name == ".git" or not child.is_dir():
                    continue
                resolved = control._authorize_repository(child, principal)
            except (OSError, RuntimeError, ValueError, PolicyDeniedError):
                continue
            entries.append(DirectoryEntry(name=child.name, path=str(resolved)))
        entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        return RepositoryDirectoryResponse(path=str(directory), parent=parent, items=entries)
    except PolicyDeniedError:
        raise
    except PermissionError as exc:
        raise HTTPException(403, "无法读取此文件夹，请选择其他目录。") from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(422, "文件夹不可用，请刷新后重新选择。") from exc
