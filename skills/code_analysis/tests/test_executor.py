import tempfile
from pathlib import Path

from skills.code_analysis.executor import run


def test_python_analysis_finds_class_and_function():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "service.py")
        src.write_text("class UserService:\n    def list_users(self):\n        return self.find_all()\n", encoding="utf-8")
        result = run({"repo_path": tmp, "language": "python"})
        assert result["status"] == "ok"
        assert "UserService" in result["data"]["classes"]
        assert "list_users" in result["data"]["functions"]
        assert ("list_users", "find_all") in result["data"]["call_hints"]
