import tempfile
from pathlib import Path

from skills.project_context.executor import run


def test_python_project_detection():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        Path(tmp, "src").mkdir()
        result = run({"repo_path": tmp})
        assert result["status"] == "ok"
        assert result["data"]["project_type"] == "python"
        assert result["data"]["build_tool"] == "poetry"


def test_missing_repo_returns_error():
    result = run({"repo_path": "Z:/no/such/path"})
    assert result["status"] == "error"
