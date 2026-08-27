import tempfile
from pathlib import Path

from skills.code_analysis.executor import run
from skills.bug_detection.executor import run as run_bug_detection
from skills.security_scan.executor import run as run_security_scan


def test_python_analysis_finds_class_and_function():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "service.py")
        src.write_text("class UserService:\n    def list_users(self):\n        return self.find_all()\n", encoding="utf-8")
        result = run({"repo_path": tmp, "language": "python"})
        assert result["status"] == "ok"
        assert "UserService" in result["data"]["classes"]
        assert "list_users" in result["data"]["functions"]
        assert ("list_users", "find_all") in result["data"]["call_hints"]


def test_java_analysis_keeps_declared_field_type_and_source_under_hidden_parent(tmp_path):
    repo = tmp_path / ".devpilot" / "workspaces" / "task" / "worktree"
    source = repo / "src" / "test" / "java" / "TestServiceTest.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class CalculatorServiceTest {\n"
        "  private TestService calculatorService;\n"
        "  void testDivide() {\n"
        "    assertEquals(0, calculatorService.divide(1, 2));\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    issue_source = repo / "src" / "main" / "java" / "Risky.java"
    issue_source.parent.mkdir(parents=True)
    issue_source.write_text(
        'class Risky {\n  String password = "real-secret";\n'
        "  void load() { for (;;) { repository.findAll(); } }\n}\n",
        encoding="utf-8",
    )

    analysis = run({"repo_path": str(repo), "language": "java"})
    assert analysis["status"] == "ok"
    assert {
        "path": str(source),
        "type": "TestService",
        "name": "calculatorService",
        "line": 2,
    } in analysis["data"]["fields"]
    test_entry = next(item for item in analysis["data"]["files"] if item["path"] == str(source))
    assert "assertEquals(0, calculatorService.divide(1, 2))" in test_entry["source_excerpt"]
    assert run_bug_detection({"repo_path": str(repo), "language": "java"})["data"]["issues"]
    assert run_security_scan({"repo_path": str(repo)})["data"]["issues"]
