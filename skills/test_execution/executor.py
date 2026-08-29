import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def _text_tail(value: str | bytes | None, limit: int = 8000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-limit:]


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
    if (cwd / "build.gradle").exists() or (cwd / "build.gradle.kts").exists():
        gradle = shutil.which("gradle")
        if gradle:
            return _launch_command(gradle, "test")
        if os.name == "nt" and (cwd / "gradlew.bat").is_file():
            return _launch_command(cwd / "gradlew.bat", "test")
        if (cwd / "gradlew").is_file():
            return _launch_command(cwd / "gradlew", "test")
        return ["gradle", "test"]
    if (cwd / "package.json").exists():
        npm = shutil.which("npm")
        return _launch_command(npm or "npm", "test")
    if (cwd / "go.mod").exists():
        return [shutil.which("go") or "go", "test", "./..."]
    if (cwd / "Cargo.toml").exists():
        return [shutil.which("cargo") or "cargo", "test", "--quiet"]
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
                "stdout": _text_tail(proc.stdout),
                "stderr": _text_tail(proc.stderr),
            },
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "ok",
            "data": {
                "passed": False,
                "exit_code": -1,
                "stdout": _text_tail(exc.stdout),
                "stderr": f"timeout after {timeout}s",
            },
        }
    except FileNotFoundError as exc:
        return {"status": "error", "error": f"command not found: {exc}"}


if __name__ == "__main__":
    print(json.dumps(run(json.loads(input())), ensure_ascii=False))
