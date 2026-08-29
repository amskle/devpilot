import tempfile
from pathlib import Path

import pytest

from skills.security_scan.executor import run


def test_detects_secret_and_sql_concatenation():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "app.py").write_text(
            "password = 'SuperSecret123'\nquery = 'SELECT * FROM user WHERE id=' + user_id\n", encoding="utf-8"
        )
        result = run({"repo_path": tmp})
        issues = result["data"]["issues"]
        assert any(i["issue"] == "hardcoded-secret" for i in issues)
        assert any(i["issue"] == "sql-injection-candidate" for i in issues)


def test_does_not_follow_source_symlinks_outside_repository():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
        external = Path(outside, "external.py")
        external.write_text("password = 'ExternalSecret123'\n", encoding="utf-8")
        link = Path(tmp, "linked.py")
        try:
            link.symlink_to(external)
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        result = run({"repo_path": tmp})

        assert result["data"]["issues"] == []
