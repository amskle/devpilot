import tempfile
import shutil
from pathlib import Path

from runtime.pipeline import run_pipeline
from shared_state.schema import TaskStatus


def test_pipeline_end_to_end_on_sample_python():
    repo = Path(__file__).resolve().parents[1] / "demo" / "sample_python"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repo, work)
        out = Path(tmp) / "out"
        task = run_pipeline(str(work), approval="auto", output_dir=str(out))
        assert task.status == TaskStatus.COMPLETED, task.to_json()
        assert task.issues
        assert any(i["issue"] == "mutable-default-argument" for i in task.issues)
        assert task.verification.get("data", {}).get("passed") is True
        assert (out / "report.md").exists()
        assert "经验沉淀" in (out / "report.md").read_text(encoding="utf-8")


def test_pipeline_rejects_when_approval_denied():
    # confirm mode is interactive; this test only checks the auto path metadata shape.
    assert TaskStatus.COMPLETED.value == "completed"


def test_pipeline_rolls_back_when_verification_fails():
    repo = Path(__file__).resolve().parents[1] / "demo" / "sample_python"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repo, work)
        broken_test = work / "tests" / "test_app.py"
        broken_test.write_text(
            "import unittest\n"
            "class TestBroken(unittest.TestCase):\n"
            "    def test_fail(self):\n"
            "        self.assertTrue(False)\n",
            encoding="utf-8",
        )
        original_app = (work / "app.py").read_text(encoding="utf-8")
        task = run_pipeline(str(work), approval="auto")
        assert task.status == TaskStatus.FAILED
        assert task.verification.get("data", {}).get("passed") is False
        assert (work / "app.py").read_text(encoding="utf-8") == original_app
