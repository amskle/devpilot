import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def _launch_command(executable: str | Path, *arguments: str) -> list[str] | str:
    resolved = str(executable)
    if os.name == "nt" and Path(resolved).suffix.lower() in {".bat", ".cmd"}:
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        launcher = subprocess.list2cmdline([comspec])
        batch_command = subprocess.list2cmdline([resolved, *arguments])
        # Popen must receive this as one command line. Passing it as a list makes
        # Python escape the quotes around a batch-file path, which cmd.exe then
        # interprets literally (and truncates paths containing spaces).
        return f'{launcher} /d /s /c "{batch_command}"'
    return [resolved, *arguments]


def _detect_command(cwd: Path) -> list[str] | str:
    if (cwd / "pyproject.toml").exists() or (cwd / "requirements.txt").exists():
        return [sys.executable, "-m", "pytest", "-q"]
    if (cwd / "pom.xml").exists():
        maven = shutil.which("mvn")
        if maven:
            return _launch_command(maven, "-q", "test")
        if os.name == "nt" and (cwd / "mvnw.cmd").is_file():
            return _launch_command(cwd / "mvnw.cmd", "-q", "test")
        if (cwd / "mvnw").is_file():
            return _launch_command(cwd / "mvnw", "-q", "test")
        return ["mvn", "-q", "test"]
    if (cwd / "package.json").exists():
        npm = shutil.which("npm")
        return _launch_command(npm or "npm", "test")
    return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]


def run(inputs: dict) -> dict:
    cwd = Path(inputs.get("cwd", ""))
    if not cwd.exists():
        return {"status": "error", "error": f"cwd not found: {cwd}"}
    configured_command = inputs.get("command")
    command = (
        shlex.split(str(configured_command))
        if configured_command
        else _detect_command(cwd)
    )
    timeout = int(inputs.get("timeout", 120))
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "ok",
            "data": {
                "passed": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout[-8000:],
                "stderr": proc.stderr[-8000:],
            },
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "ok",
            "data": {
                "passed": False,
                "exit_code": -1,
                "stdout": (exc.stdout or "")[-8000:],
                "stderr": f"timeout after {timeout}s",
            },
        }
    except FileNotFoundError as exc:
        return {"status": "error", "error": f"command not found: {exc}"}


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
