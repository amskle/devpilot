from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devpilot.api import create_app
from devpilot.api.core.config import ApiSettings, Principal
from devpilot.service import TaskService


@pytest.fixture
def browser(tmp_path):
    root = tmp_path / "repos"
    root.mkdir()
    (root / "项目 空格").mkdir()
    (root / ".git").mkdir()
    (root / "file.txt").write_text("not a directory")
    settings = ApiSettings(
        tokens={"creator": Principal("creator", can_create_tasks=True),
                "reader": Principal("reader"), "admin": Principal("admin", True)},
        repository_roots=(root,),
    )
    service = TaskService(data_dir=tmp_path / "data")
    try:
        with TestClient(create_app(service=service, settings=settings)) as client:
            yield client, root
    finally:
        service.close()


def test_browser_requires_authentication_and_creation_permission(browser):
    client, _ = browser
    assert client.get("/api/repositories/directories").status_code == 401
    assert client.get("/api/repositories/directories", headers={
        "Authorization": "Bearer reader",
    }).status_code == 403


def test_browse_roots_children_and_parent_with_exact_paths(browser):
    client, root = browser
    headers = {"Authorization": "Bearer creator"}
    roots = client.get("/api/repositories/directories", headers=headers).json()
    assert roots == {"path": None, "parent": None,
                     "items": [{"name": "repos", "path": str(root.resolve())}]}
    listing = client.get("/api/repositories/directories", headers=headers,
                         params={"path": str(root)}).json()
    assert listing["parent"] is None
    assert listing["items"] == [{"name": "项目 空格", "path": str((root / "项目 空格").resolve())}]
    child = client.get("/api/repositories/directories", headers=headers,
                       params={"path": listing["items"][0]["path"]}).json()
    assert child["parent"] == str(root.resolve())
    assert child["items"] == []


@pytest.mark.parametrize("token", ["creator", "admin"])
def test_browser_rejects_escape_relative_missing_and_file_paths(browser, token):
    client, root = browser
    headers = {"Authorization": f"Bearer {token}"}
    for path, expected in [(root / "..", 403), (Path("relative"), 422),
                           (root / "missing", 422), (root / "file.txt", 422)]:
        response = client.get("/api/repositories/directories", headers=headers,
                              params={"path": str(path)})
        assert response.status_code == expected, response.text


def test_browser_hides_and_rejects_symlinks_outside_roots(browser, tmp_path):
    client, root = browser
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require host permissions")
    headers = {"Authorization": "Bearer creator"}
    listing = client.get("/api/repositories/directories", headers=headers,
                         params={"path": str(root)}).json()
    assert "escape" not in [entry["name"] for entry in listing["items"]]
    assert client.get("/api/repositories/directories", headers=headers,
                      params={"path": str(link)}).status_code == 403


def test_browser_handles_filesystem_permission_errors(browser, monkeypatch):
    client, root = browser

    def denied(_):
        raise PermissionError("private filesystem details")

    monkeypatch.setattr(Path, "iterdir", denied)
    response = client.get("/api/repositories/directories",
                          headers={"Authorization": "Bearer creator"},
                          params={"path": str(root)})
    assert response.status_code == 403
    assert "private filesystem details" not in response.text
