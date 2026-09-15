from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from devpilot import telemetry
from devpilot.agents.model_gateway import OpenAICompatibleGateway
from devpilot.domain.models import PlanDraft


class RecordingClient:
    def __init__(self):
        self.observations = []

    def flush(self):
        pass

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        record = {**kwargs, "ended": False}
        self.observations.append(record)

        def update(**changes):
            assert not record["ended"], "update must occur before observation ends"
            if "metadata" in changes:
                changes["metadata"] = {**record.get("metadata", {}), **changes["metadata"]}
            record.update(changes)

        try:
            yield SimpleNamespace(update=update)
        finally:
            record["ended"] = True


def test_missing_credentials_and_disabled_tracing_do_not_initialize_sdk(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(telemetry, "_configured_client", lambda: pytest.fail("SDK initialized"))
    assert not telemetry.telemetry_enabled()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    with telemetry._observation(name="disabled") as observation:
        observation.update(output="still works")


@pytest.mark.parametrize("stage", ["create", "enter", "update", "exit"])
def test_tracing_failures_do_not_break_application_execution(monkeypatch, stage):
    class BrokenManager:
        def __enter__(self):
            if stage == "enter":
                raise RuntimeError("telemetry unavailable")
            return self

        def update(self, **kwargs):
            if stage == "update":
                raise RuntimeError("telemetry unavailable")

        def __exit__(self, *args):
            if stage == "exit":
                raise RuntimeError("telemetry unavailable")

    def start(**kwargs):
        if stage == "create":
            raise RuntimeError("telemetry unavailable")
        return BrokenManager()

    monkeypatch.setattr(telemetry, "_client", lambda: SimpleNamespace(start_as_current_observation=start))
    executed = []
    with telemetry._observation(name="example") as observation:
        executed.append("once")
        observation.update(output="success")
    assert executed == ["once"]
    original = ValueError("application error")
    with pytest.raises(ValueError) as caught:
        with telemetry._observation(name="example"):
            raise original
    assert caught.value is original


def test_node_observation_runs_node_and_records_output_before_end(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(telemetry, "_client", lambda: client)
    node = telemetry.node_observation("planning", lambda state: state)(
        lambda state: {"status": "COMPLETED"}
    )
    assert node({"status": "RUNNING"}) == {"status": "COMPLETED"}
    assert client.observations == [{
        "name": "generate-plan", "as_type": "span", "input": {"status": "RUNNING"},
        "output": {"status": "COMPLETED"}, "ended": True,
    }]


def test_retrieval_observation_uses_specific_type_and_coverage_metadata(
    tmp_path, monkeypatch
):
    from devpilot.domain.models import ExecutionBudget, WorkspaceRef
    from devpilot.tools.executor import ToolExecutor, build_default_registry

    (tmp_path / "app.py").write_text("needle = True\n", encoding="utf-8")
    workspace = WorkspaceRef(
        workspace_id="ws",
        repository_id="repo",
        worktree_ref=str(tmp_path),
        baseline_revision="abc123",
        current_revision="abc123",
        lease_owner="run",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    client = RecordingClient()
    monkeypatch.setattr(telemetry, "_client", lambda: client)

    ToolExecutor(build_default_registry()).execute(
        "repo-retrieval",
        {"workspace_id": "ws", "query": "needle"},
        workspace=workspace,
        allowed_tools=("repo-retrieval",),
        agent_id="planning",
        operation_id="retrieve-once",
        execution_budget=ExecutionBudget().to_state_dict(),
    )

    observation = client.observations[0]
    assert observation["as_type"] == "retriever"
    assert observation["name"] == "repo-retrieval"
    assert observation["metadata"]["repository_revision"] == "abc123"
    assert observation["metadata"]["selected_chunks"] == 1
    assert observation["metadata"]["corpus_truncated"] is False


def test_mask_removes_secrets_without_changing_model_inputs(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-sensitive")
    source = {"api_key": "secret", "messages": [{"content": "copied sk-lf-test-sensitive Bearer private-token-123"}]}
    masked = telemetry.mask_telemetry_data(data=source)
    assert masked["api_key"] == "[REDACTED]"
    assert "sk-lf-test-sensitive" not in str(masked)
    assert "private-token-123" not in str(masked)
    assert source["api_key"] == "secret"
    assert "sk-lf-test-sensitive" in source["messages"][0]["content"]


@pytest.mark.parametrize("traced", [False, True])
def test_gateway_only_sends_langfuse_parameters_to_instrumented_sdk(monkeypatch, traced):
    from devpilot.agents import model_gateway

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(
                                   prompt_tokens=5,
                                   completion_tokens=3,
                                   total_tokens=12,
                               ),
                               choices=[SimpleNamespace(message=SimpleNamespace(content="{}", tool_calls=[]))])

    monkeypatch.setattr(telemetry, "telemetry_enabled", lambda: traced)
    monkeypatch.setattr(model_gateway, "_openai_client_class", lambda **kwargs: lambda **options: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    gateway = OpenAICompatibleGateway(model="test-model", api_key="test-model-key")
    response = gateway.complete(agent_id="planning", messages=[], tools=[],
                                output_model=PlanDraft, timeout_seconds=10,
                                max_completion_tokens=4096, node="planning", turn=1)
    assert ("name" in calls[0]) is traced
    assert calls[0]["max_completion_tokens"] == 4096
    assert response.usage.prompt_tokens == 5
    assert response.usage.completion_tokens == 7
    assert response.usage.reported_total_tokens == 12


def test_traced_graph_records_actual_task_model_and_final_state(tmp_path, monkeypatch):
    from devpilot.agents.model_gateway import ModelResponse, ScriptedFakeModelGateway
    from devpilot.service import TaskService
    from devpilot.testing.repo import make_test_repo

    client = RecordingClient()
    monkeypatch.setattr(telemetry, "_client", lambda: client)
    monkeypatch.setattr(telemetry, "_attributes", lambda **kwargs: nullcontext())
    gateway = ScriptedFakeModelGateway({
        "planning": [ModelResponse.final({"summary": "inspect", "tasks": [{"id": "inspect"}], "acceptance_criteria": ["unchanged"], "risks": []})],
        "diagnosis": [ModelResponse.final({"outcome": "NO_ACTION_REQUIRED", "summary": "correct", "issues": []})],
        "review": [ModelResponse.final({"summary": "no changes", "outcome": "NO_CHANGES", "lessons": []})],
    })
    service = TaskService(data_dir=tmp_path / "data", gateway_factory=lambda model: gateway, model="default-model")
    try:
        state = service.create_task(make_test_repo(tmp_path / "source"), "inspect this fixture", model="selected-model")
        root = next(item for item in client.observations if item["name"] == "devpilot-task-run")
        assert root["as_type"] == "agent"
        assert root["metadata"]["model"] == "selected-model"
        assert root["metadata"]["app_version"]
        assert root["output"]["status"] == state["status"] == "COMPLETED_NO_CHANGES"
        assert root["input"] == "inspect this fixture"
        assert len(root["metadata"]["baseline_revision"]) == 40
        assert all(item["ended"] for item in client.observations)
        child_agents = [
            item
            for item in client.observations
            if item["as_type"] == "agent" and item["name"] != "devpilot-task-run"
        ]
        assert len(child_agents) == 3
        gateway.assert_consumed()
    finally:
        service.close()
