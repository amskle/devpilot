import tempfile
import os
import subprocess
from pathlib import Path

from skills.test_execution.executor import _detect_command, run


def test_passing_command():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "test_demo.py").write_text(
            "import unittest\nclass DemoTest(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        result = run({"command": "python -m unittest discover -s . -q", "cwd": tmp, "timeout": 30})
        assert result["status"] == "ok"
        assert result["data"]["passed"] is True


def test_failing_command():
    result = run({"command": "python -c \"raise SystemExit(3)\"", "cwd": tempfile.gettempdir(), "timeout": 30})
    assert result["data"]["passed"] is False
    assert result["data"]["exit_code"] == 3


def test_windows_maven_cmd_is_launched_through_comspec(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    maven = tmp_path / "Maven Bin" / "mvn.CMD"
    maven.parent.mkdir()
    maven.write_text("@exit /b 0\n", encoding="utf-8")
    monkeypatch.setattr("skills.test_execution.executor.os.name", "nt")
    monkeypatch.setattr("skills.test_execution.executor.shutil.which", lambda name: str(maven))
    command = _detect_command(tmp_path)
    assert command == (
        f'{os.environ.get("COMSPEC", "cmd.exe")} /d /s /c '
        f'"{subprocess.list2cmdline([str(maven), "-q", "test"])}"'
    )
    result = run({"cwd": str(tmp_path), "timeout": 30})
    assert result["status"] == "ok"
    assert result["data"]["passed"] is True
