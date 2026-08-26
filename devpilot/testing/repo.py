from __future__ import annotations

import subprocess
from pathlib import Path


def make_test_repo(path: Path) -> Path:
    path.mkdir()
    (path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_basic.py").write_text(
        "import unittest\nclass TestBasic(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
        encoding="utf-8",
    )
    for args in (
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("add", "-A"),
        ("commit", "-m", "baseline"),
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)
    return path
