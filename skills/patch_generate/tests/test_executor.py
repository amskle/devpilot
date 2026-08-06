import tempfile
from pathlib import Path

from skills.patch_generate.executor import run


def test_generates_diff_without_writing():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp, "demo.py")
        target.write_text("value = 1\n", encoding="utf-8")
        result = run({"target_file": str(target), "replacements": [{"old": "value = 1", "new": "value = 2"}]})
        assert result["status"] == "ok"
        assert result["data"]["applied"] is False
        assert "-value = 1" in result["data"]["diff"]
        assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp, "demo.py")
        target.write_text("value = 1\n", encoding="utf-8")
        result = run(
            {
                "target_file": str(target),
                "replacements": [{"old": "value = 1", "new": "value = 2"}],
                "apply": True,
            }
        )
        assert result["data"]["applied"] is True
        assert target.read_text(encoding="utf-8") == "value = 2\n"
