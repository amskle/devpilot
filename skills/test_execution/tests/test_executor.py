import tempfile
from pathlib import Path

from skills.test_execution.executor import run


def test_passing_command():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        result = run({"command": "python -m unittest discover -s . -q", "cwd": tmp, "timeout": 30})
        assert result["status"] == "ok"
        assert result["data"]["passed"] is True


def test_failing_command():
    result = run({"command": "python -c \"raise SystemExit(3)\"", "cwd": tempfile.gettempdir(), "timeout": 30})
    assert result["data"]["passed"] is False
    assert result["data"]["exit_code"] == 3
