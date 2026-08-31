from pathlib import Path

from scripts.build_resume_evaluation_fixtures import (
    SMOKE_CASE_IDS,
    _promote_staging,
    _risk_decision,
    build_cases,
)


def test_resume_evaluation_catalog_is_isolated_and_policy_aligned():
    cases = build_cases()

    assert len(cases) == 20
    assert len({case.case_id for case in cases}) == 20
    assert sum(case.baseline_passes for case in cases) == 4
    assert sum(case.expected_status == "COMPLETED" for case in cases) == 10
    assert sum(case.expected_status == "WAITING_RISK_APPROVAL" for case in cases) == 3
    assert sum(case.expected_status == "POLICY_REJECTED" for case in cases) == 3

    for case in cases:
        assert case.changed_files == sorted(case.changed_files)
        assert len(case.changed_files) <= 1
        assert _risk_decision(case) == case.expected_risk_decision

    rejected = [case for case in cases if case.expected_status == "POLICY_REJECTED"]
    assert all(case.changed_files == ["app_secrets/auth.py"] for case in rejected)
    assert all(case.verification_passed is False for case in rejected)
    assert all(".gitignore" in case.files for case in cases)
    assert set(SMOKE_CASE_IDS).issubset({case.case_id for case in cases})


def test_fixture_promotion_falls_back_to_child_moves_on_windows(tmp_path, monkeypatch):
    staging = tmp_path / ".staging"
    target = tmp_path / "target"
    staging.mkdir()
    (staging / "fixture.txt").write_text("verified\n", encoding="utf-8")

    def deny_replace(self, destination):
        raise PermissionError("simulated Windows directory rename denial")

    monkeypatch.setattr(Path, "replace", deny_replace)
    _promote_staging(staging, target)

    assert (target / "fixture.txt").read_text(encoding="utf-8") == "verified\n"
