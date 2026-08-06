import shlex
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("devpilot-testing")


def _detect_command(cwd: Path) -> str:
    if (cwd / "pyproject.toml").exists() or (cwd / "requirements.txt").exists():
        return "python -m pytest -q"
    if (cwd / "pom.xml").exists():
        return "mvn -q test"
    if (cwd / "package.json").exists():
        return "npm test"
    return "python -m unittest discover -s tests -q"


@mcp.tool()
def detect_command(cwd: str) -> str:
    """Detect the appropriate test command for a project."""
    return _detect_command(Path(cwd))


@mcp.tool()
def run_test(cwd: str, command: str = "", timeout: int = 120) -> str:
    """Run a test command and return exit code plus captured output."""
    workdir = Path(cwd)
    if not workdir.exists():
        return f"error: cwd not found: {cwd}"
    cmd = command or _detect_command(workdir)
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        payload = {
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "command": cmd,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        }
        return f"{'PASS' if proc.returncode == 0 else 'FAIL'}\n{payload}"
    except subprocess.TimeoutExpired as exc:
        return f"TIMEOUT after {timeout}s\nstdout_tail={exc.stdout}"
    except FileNotFoundError as exc:
        return f"error: {exc}"


if __name__ == "__main__":
    mcp.run()
