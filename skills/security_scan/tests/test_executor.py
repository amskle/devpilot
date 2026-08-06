import tempfile
from pathlib import Path

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
