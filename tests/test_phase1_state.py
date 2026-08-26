import json

import pytest

from devpilot.domain.state import create_initial_state, migrate_state, replace_progress_window, validate_state
from devpilot.domain.progress import evaluate_progress_signals
from runtime.compat_pipeline import legacy_status
from shared_state.schema import TaskStatus as LegacyTaskStatus


def _assert_plain(value):
    assert not hasattr(value, "model_dump")
    if isinstance(value, dict):
        for item in value.values():
            _assert_plain(item)
    elif isinstance(value, list):
        for item in value:
            _assert_plain(item)
    else:
        assert value is None or isinstance(value, (str, int, float, bool))


def test_initial_state_is_complete_independent_and_plain_json():
    first = create_initial_state("task-a", "run-a")
    second = create_initial_state("task-b", "run-b")
    first["progress_window"]["entries"].append({"x": 1})
    assert second["progress_window"]["entries"] == []
    _assert_plain(first)
    assert json.loads(json.dumps(first))["schema_version"] == 1
    assert set(first) == set(validate_state(first))


def test_unknown_state_version_fails_safely():
    state = create_initial_state("task", "run")
    state["schema_version"] = 999
    with pytest.raises(ValueError, match="no safe migration"):
        migrate_state(state)


def test_progress_window_is_bounded_and_replaced():
    state = create_initial_state("task", "run")
    for index in range(10):
        state["progress_window"] = replace_progress_window(state, {"round": index}, made_progress=False)
    assert [item["round"] for item in state["progress_window"]["entries"]] == [4, 5, 6, 7, 8, 9]
    assert state["progress_window"]["no_progress_rounds"] == 10


def test_progress_detects_same_failure_repeated_change_and_aba():
    same = evaluate_progress_signals(
        [{"symptom_fingerprint": "A", "change_fingerprint": "X"}],
        {"symptom_fingerprint": "A", "change_fingerprint": "X"},
    )
    assert same.same_symptom and same.repeated_change and not same.made_progress

    symptom_aba = evaluate_progress_signals(
        [
            {"symptom_fingerprint": "A", "change_fingerprint": "X"},
            {"symptom_fingerprint": "B", "change_fingerprint": "Y"},
        ],
        {"symptom_fingerprint": "A", "change_fingerprint": "Z"},
    )
    assert symptom_aba.symptom_aba and not symptom_aba.made_progress

    change_aba = evaluate_progress_signals(
        [
            {"symptom_fingerprint": "A", "change_fingerprint": "X"},
            {"symptom_fingerprint": "B", "change_fingerprint": "Y"},
        ],
        {"symptom_fingerprint": "C", "change_fingerprint": "X"},
    )
    assert change_aba.change_aba and not change_aba.made_progress


@pytest.mark.parametrize(
    ("status", "node", "legacy"),
    [
        ("CREATED", "workspace_setup", LegacyTaskStatus.ANALYZING),
        ("RUNNING", "diagnosis", LegacyTaskStatus.PLANNING),
        ("WAITING_RISK_APPROVAL", "approval_gate", LegacyTaskStatus.AWAITING_APPROVAL),
        ("COMPLETED_NO_CHANGES", "review", LegacyTaskStatus.COMPLETED),
        ("POLICY_REJECTED", "risk_assessment", LegacyTaskStatus.FAILED),
        ("CANCELLED", "approval_gate", LegacyTaskStatus.FAILED),
    ],
)
def test_legacy_status_mapping_is_explicit(status, node, legacy):
    assert legacy_status({"status": status, "current_node": node}) == legacy
