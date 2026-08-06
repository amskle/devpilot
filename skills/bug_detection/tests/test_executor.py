import tempfile
from pathlib import Path

from skills.bug_detection.executor import run


def test_detects_mutable_default_and_n1():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "buggy.py").write_text("def f(items=[]):\n    return items\n", encoding="utf-8")
        Path(tmp, "UserRepository.java").write_text(
            "for (User u : users) { return userRepository.findByEmail(u.getEmail()); }\n", encoding="utf-8"
        )
        result = run({"repo_path": tmp, "language": "auto"})
        issues = result["data"]["issues"]
        assert any(i["issue"] == "mutable-default-argument" for i in issues)
        assert any(i["issue"] == "n-plus-one-candidate" for i in issues)
