from __future__ import annotations

from typing import Any, Callable, Mapping

from langgraph.graph import END, START, StateGraph

from devpilot.domain.models import TaskStatus
from devpilot.domain.state import GraphState


Node = Callable[[GraphState], GraphState]


def _diagnosis_route(state: GraphState) -> str:
    if state["status"] == TaskStatus.WAITING_HUMAN_INTERVENTION.value:
        return "end"
    if (state["diagnosis"] or {}).get("outcome") == "PLAN_INVALID":
        return "plan_invalid"
    if (
        state["latest_failure"] is not None
        and (state["diagnosis"] or {}).get("outcome") == "NO_ACTION_REQUIRED"
    ):
        return "failed_no_action"
    if (state["diagnosis"] or {}).get("outcome") == "NO_ACTION_REQUIRED":
        return "review"
    return "patch_generation"


def _risk_route(state: GraphState) -> str:
    if state["status"] == TaskStatus.POLICY_REJECTED.value:
        return "end"
    if state["status"] == TaskStatus.WAITING_RISK_APPROVAL.value:
        return "approval"
    return "apply"


def _approval_route(state: GraphState) -> str:
    return "apply" if state["status"] == TaskStatus.RUNNING.value else "end"


def _verification_route(state: GraphState) -> str:
    return "review" if state["latest_failure"] is None else "evaluate"


def _failure_route(state: GraphState) -> str:
    if state["status"] != TaskStatus.RUNNING.value:
        return "end"
    if (state["latest_failure"] or {}).get("recovery_action") == "REPLAN":
        return "prepare_replan"
    return "diagnosis"


def compile_graph(nodes: Mapping[str, Node], checkpointer: Any):
    """Wire node implementations into the stable DevPilot state-machine topology."""

    builder = StateGraph(GraphState)
    for name, node in nodes.items():
        builder.add_node(name, node)

    builder.add_edge(START, "workspace_setup")
    builder.add_edge("workspace_setup", "baseline_context")
    builder.add_edge("baseline_context", "baseline_verification")
    builder.add_edge("baseline_verification", "planning")
    builder.add_edge("planning", "diagnosis")
    builder.add_conditional_edges(
        "diagnosis",
        _diagnosis_route,
        {
            "end": END,
            "plan_invalid": "failure_router",
            "failed_no_action": "failure_router",
            "review": "review",
            "patch_generation": "patch_generation",
        },
    )
    builder.add_edge("patch_generation", "risk_assessment")
    builder.add_conditional_edges(
        "risk_assessment",
        _risk_route,
        {"end": END, "approval": "approval_gate", "apply": "apply_patch"},
    )
    builder.add_conditional_edges(
        "approval_gate",
        _approval_route,
        {"apply": "apply_patch", "end": END},
    )
    builder.add_edge("apply_patch", "run_verification")
    builder.add_edge("run_verification", "parse_verification")
    builder.add_conditional_edges(
        "parse_verification",
        _verification_route,
        {"review": "review", "evaluate": "evaluate_progress"},
    )
    builder.add_edge("evaluate_progress", "failure_router")
    builder.add_conditional_edges(
        "failure_router",
        _failure_route,
        {
            "prepare_replan": "prepare_replan",
            "diagnosis": "diagnosis",
            "end": END,
        },
    )
    builder.add_edge("prepare_replan", "planning")
    builder.add_edge("review", END)
    return builder.compile(checkpointer=checkpointer)
