import json
import shlex
import subprocess
from pathlib import Path


def _detect_command(cwd: Path) -> str:
    if (cwd / "pyproject.toml").exists() or (cwd / "requirements.txt").exists():
        return "python -m pytest -q"
    if (cwd / "pom.xml").exists():
        return "mvn -q test"
    if (cwd / "package.json").exists():
        return "npm test"
    return "python -m unittest discover -s tests -q"


def run(inputs: dict) -> dict:
    cwd = Path(inputs.get("cwd", ""))
    if not cwd.exists():
        return {"status": "error", "error": f"cwd not found: {cwd}"}
    command = inputs.get("command") or _detect_command(cwd)
    timeout = int(inputs.get("timeout", 120))
    try:
        proc = subprocess.run(
            shlex.split(command),
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
