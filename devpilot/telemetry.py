"""Langfuse tracing for DevPilot task runs.

Tracing is optional and fail-open: when Langfuse is not installed, credentials are
missing, or tracing is disabled, every helper here degrades to a no-op context
manager so the runtime behaves exactly as it did before instrumentation.

Trace shape produced for one graph invocation (one ``run_id``)::

    devpilot-task-run                  # root agent; owns the trace input/output
    |- workspace-setup
    |- build-baseline-context
    |- generate-plan
    |  |- planning                     # agent
    |     |- generate-planning         # generation (per model call)
    |     |- project-context           # tool
    |- diagnose-issue
    |  |- diagnosis                    # agent
    |     |- generate-diagnosis
    |     |- code-analysis             # tool
    ...

Task runs that belong to the same DevPilot task share a Langfuse ``session_id``
(``task_id``), and ``run_id`` stays in trace metadata so resumed, replanned, and
approval-driven runs remain distinguishable.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Iterator, Literal, Protocol

from devpilot.events.redaction import sanitize_event_value

LOGGER = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _application_version() -> str:
    try:
        return version("devpilot-infra")
    except PackageNotFoundError:
        return "unknown"

__all__ = [
    "agent_observation",
    "agent_span_name",
    "flush_telemetry",
    "graph_node_summary",
    "node_observation",
    "node_span_name",
    "observation_output_metadata",
    "task_run_observation",
    "telemetry_enabled",
    "tool_observation",
]


class Observation(Protocol):
    """The subset of the Langfuse observation API DevPilot relies on."""

    def update(self, **kwargs: Any) -> Any: ...


class _NullObservation:
    """Stand-in observation used whenever tracing is disabled."""

    __slots__ = ()

    def update(self, **kwargs: Any) -> None:
        return None


_NULL_OBSERVATION = _NullObservation()


class _SafeObservation:
    def __init__(self, observation: Any):
        self.observation = observation

    def update(self, **kwargs: Any) -> None:
        try:
            self.observation.update(**kwargs)
        except Exception:
            LOGGER.warning("Could not update Langfuse observation; execution continues")


def mask_telemetry_data(*, data: Any, **kwargs: Any) -> Any:
    """Reuse audit redaction and remove configured credentials even inside text."""
    secrets = [value for key, value in os.environ.items()
               if key.startswith(("DEVPILOT_", "LANGFUSE_"))
               and any(part in key for part in ("KEY", "TOKEN", "SECRET"))
               and len(value) >= 8]

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
        elif isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        elif isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(sanitize_event_value(data))


@functools.lru_cache(maxsize=1)
def _configured_client() -> Any:
    # Initialize before importing the OpenAI integration so it shares this mask.
    from langfuse import Langfuse

    return Langfuse(
        mask=mask_telemetry_data,
        environment=os.environ.get("LANGFUSE_TRACING_ENVIRONMENT")
        or os.environ.get("DEVPILOT_ENV", "development"),
    )


# Graph node ids are already stable, low-cardinality, and verb-ish. These names
# make the trace tree read as a description of what happened.
_NODE_NAMES: dict[str, str] = {
    "workspace_setup": "workspace-setup",
    "baseline_context": "build-baseline-context",
    "baseline_verification": "verify-baseline",
    "prepare_replan": "prepare-replan",
    "planning": "generate-plan",
    "diagnosis": "diagnose-issue",
    "patch_generation": "generate-patch",
    "risk_assessment": "assess-risk",
    "approval_gate": "await-approval",
    "apply_patch": "apply-patch",
    "run_verification": "run-verification",
    "parse_verification": "parse-verification",
    "evaluate_progress": "evaluate-progress",
    "failure_router": "route-failure",
    "review": "review-outcome",
}

# Agent ids are stable and low-cardinality; used verbatim as observation names.
_AGENT_NAMES: dict[str, str] = {
    "planning": "planning",
    "diagnosis": "diagnosis",
    "patch_generation": "patch_generation",
    "review": "review",
}

def _client() -> Any | None:
    """Return a configured Langfuse client, or ``None`` when tracing is off."""

    if os.environ.get("LANGFUSE_TRACING_ENABLED", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        return _configured_client()
    except Exception:
        LOGGER.warning("Langfuse initialization failed; execution continues")
        return None


def telemetry_enabled() -> bool:
    """Report whether a Langfuse client is available for this process."""

    return _client() is not None


@contextmanager
def _observation(**kwargs: Any) -> Iterator[Any]:
    """Start a Langfuse observation, or yield a no-op stand-in when tracing is off."""

    client = _client()
    if client is None:
        yield _NULL_OBSERVATION
        return
    try:
        manager = client.start_as_current_observation(**kwargs)
    except Exception:
        yield _NULL_OBSERVATION
        return
    with _safe_context(manager) as observation:
        yield _SafeObservation(observation) if observation is not None else _NULL_OBSERVATION


@contextmanager
def _safe_context(manager: Any) -> Iterator[Any]:
    """Telemetry failures must never swallow or replace application exceptions."""
    try:
        value = manager.__enter__()
    except Exception:
        LOGGER.warning("Could not start Langfuse context; execution continues")
        yield None
        return
    try:
        yield value
    except BaseException:
        try:
            manager.__exit__(*sys.exc_info())
        except Exception:
            LOGGER.warning("Could not close Langfuse context")
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            LOGGER.warning("Could not close Langfuse context; execution continues")


@contextmanager
def _attributes(**kwargs: Any) -> Iterator[None]:
    """Propagate trace-level attributes to all nested observations."""

    client = _client()
    if client is None:
        yield
        return
    try:
        from langfuse import propagate_attributes
        manager = propagate_attributes(**kwargs)
    except Exception:
        yield
        return
    with _safe_context(manager):
        yield


def node_span_name(node: str) -> str:
    """Map a DevPilot graph node id to its stable Langfuse observation name."""

    return _NODE_NAMES.get(node, node.replace("_", "-"))


def agent_span_name(agent_id: str) -> str:
    """Map a DevPilot agent id to its stable Langfuse observation name."""

    return _AGENT_NAMES.get(agent_id, agent_id.replace("_", "-"))


def observation_output_metadata(value: Any) -> dict[str, Any]:
    """Summarize a structured agent output so metadata never duplicates the output."""

    if isinstance(value, dict):
        return {"structured_output_keys": sorted(str(key) for key in value)}
    if isinstance(value, (list, tuple)):
        return {"structured_output_items": len(value)}
    if value is None:
        return {}
    return {"structured_output_type": type(value).__name__}


@contextmanager
def task_run_observation(
    *,
    task_id: str,
    run_id: str,
    request: str,
    model: str,
    revision: str,
    parent_run_id: str | None = None,
    resumed: bool = False,
) -> Iterator[Any]:
    """Root observation for one graph invocation; owns the trace input/output."""

    metadata: dict[str, Any] = {
        "task_id": task_id,
        "run_id": run_id,
        "model": model,
        "revision": revision,
        "app_version": _application_version(),
        "parent_run_id": parent_run_id,
        "resumed": resumed,
    }
    with _observation(
        as_type="agent",
        name="devpilot-task-run",
        input=request,
        metadata=metadata,
    ) as observation:
        with _attributes(
            trace_name="devpilot-task-run",
            session_id=task_id,
            tags=["devpilot"],
            metadata=metadata,
        ):
            yield observation


@contextmanager
def agent_observation(
    *,
    agent_id: str,
    task_id: str,
    run_id: str,
    node: str,
    attempt: int = 1,
    context_keys: list[str] | None = None,
) -> Iterator[Any]:
    """Observation for one agent invocation, typed `agent` so it drives the agent graph.

    The observation input is deliberately not the raw node context: generations
    already carry the full message payload, so the agent node records only what
    identifies the invocation.
    """

    metadata: dict[str, Any] = {
        "agent_id": agent_id,
        "node": node,
        "task_id": task_id,
        "run_id": run_id,
    }
    if attempt > 1:
        metadata["attempt"] = attempt
    if context_keys:
        metadata["context_keys"] = context_keys
    with _observation(
        as_type="agent",
        name=agent_span_name(agent_id),
        metadata=metadata,
    ) as observation:
        yield observation


@contextmanager
def tool_observation(
    *,
    name: str,
    observation_type: Literal["tool", "retriever"] = "tool",
    agent_id: str | None,
    operation_id: str,
    node: str = "",
    inputs: dict[str, Any],
) -> Iterator[Any]:
    """Observation for one physical tool or read-only retrieval execution."""

    with _observation(
        as_type=observation_type,
        name=name,
        input=inputs,
        metadata={"agent_id": agent_id, "operation_id": operation_id, "node": node},
    ) as observation:
        yield observation


def graph_node_summary(state: Any) -> dict[str, Any]:
    """Summarize graph state for one node span, keeping the raw state out of Langfuse.

    The full checkpoint payload is large, contains source excerpts, and is already
    available in DevPilot's own artifact store, so node observations carry only the
    decision-relevant fields.
    """

    if not isinstance(state, dict):
        return {}
    verification = state.get("verification") or {}
    diagnosis = state.get("diagnosis") or {}
    failure = state.get("latest_failure") or {}
    proposal = state.get("patch_proposal") or {}
    plan_ref = state.get("active_plan_ref") or {}
    summary: dict[str, Any] = {
        "status": state.get("status"),
        "current_node": state.get("current_node"),
        "state_revision": state.get("state_revision"),
        "plan_version": _mapping_get(plan_ref, "version"),
        "diagnosis_outcome": _mapping_get(diagnosis, "outcome"),
        "verification_phase": _mapping_get(verification, "phase"),
        "verification_passed": _mapping_get(verification, "passed"),
        "patch_status": _mapping_get(proposal, "status"),
        "changed_files": _mapping_get(proposal, "changed_files"),
        "failure_code": _mapping_get(failure, "error_code"),
        "recovery_action": _mapping_get(failure, "recovery_action"),
        "no_progress_rounds": _mapping_get(state.get("progress_window") or {}, "no_progress_rounds"),
        "pause_reason": state.get("pause_reason"),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _mapping_get(value: Any, key: str) -> Any:
    """Read a key from a mapping or a Pydantic model without raising."""

    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def node_observation(name: str, summarize: Any) -> Any:
    """Decorate a graph node so each execution becomes one `span` observation.

    ``summarize`` receives the incoming or outgoing state and returns the compact
    summary recorded as the observation's input or output; the raw state stays out
    of Langfuse so a trace never duplicates the full checkpoint payload.
    """

    def decorate(node: Any) -> Any:
        @functools.wraps(node)
        def wrapped(state: Any) -> Any:
            with _observation(
                as_type="span",
                name=node_span_name(name),
                input=summarize(state),
            ) as observation:
                result = node(state)
                observation.update(output=summarize(result))
            return result

        return wrapped

    return decorate


def flush_telemetry() -> None:
    """Flush queued observations; safe to call when tracing is disabled."""

    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        return
