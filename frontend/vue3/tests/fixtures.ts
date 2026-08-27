import type { ExecutionBudget, ExecutionEvent, TaskState } from "@/domain/types";

export const budget: ExecutionBudget = {
  max_iterations: 3,
  max_plan_revisions: 2,
  max_rollbacks: 2,
  max_llm_calls: 20,
  max_tool_calls: 40,
  max_total_tokens: 100000,
  max_cost: "2.0000",
  cost_currency: "USD",
  max_active_seconds: 1800,
  iterations_used: 1,
  plan_revisions_used: 0,
  rollbacks_used: 0,
  llm_calls_used: 3,
  tool_calls_used: 5,
  prompt_tokens_used: 1200,
  completion_tokens_used: 300,
  cost_used: "0.0130",
  active_seconds_used: 42,
};

export const approval = {
  approval_id: "approval_123456789",
  task_id: "task_1",
  run_id: "run_1",
  patch_ref: { sha256: "a".repeat(64) },
  patch_hash: "b".repeat(64),
  base_revision: "c".repeat(40),
  risk_report_ref: { sha256: "d".repeat(64) },
  requested_at: "2026-08-27T00:00:00Z",
  expires_at: "2099-08-28T00:00:00Z",
};

export const waitingState: TaskState = {
  schema_version: 1,
  state_revision: 7,
  task_id: "task_1",
  run_id: "run_1",
  parent_run_id: null,
  status: "WAITING_RISK_APPROVAL",
  pause_reason: "RISK_APPROVAL",
  current_node: "approval_gate",
  active_plan_ref: { sha256: "e".repeat(64) },
  diagnosis: null,
  patch_proposal: {
    patch_id: "patch_1",
    patch_ref: { sha256: "a".repeat(64) },
    patch_hash: "b".repeat(64),
    base_revision: "c".repeat(40),
    changed_files: ["src/app.py"],
    summary: "Fix validation",
    status: "WAITING_RISK_APPROVAL",
  },
  verification: null,
  execution_budget: budget,
  pending_approval: approval,
  pending_replan_request: null,
  latest_failure: null,
  active_recovery_point_ref: null,
  model_profile: { provider: "openai-compatible", model: "qwen3.7-flash" },
};

export function event(sequence: number, overrides: Partial<ExecutionEvent> = {}): ExecutionEvent {
  return {
    event_id: `event_${sequence}`,
    schema_version: 1,
    task_id: "task_1",
    run_id: "run_1",
    state_revision: sequence,
    node_name: "planning",
    attempt: 1,
    event_type: "node_completed",
    sequence_number: sequence,
    correlation_id: null,
    causation_id: null,
    payload: {},
    artifact_refs: [],
    checkpoint_confirmed: true,
    created_at: `2026-08-27T00:00:0${sequence}Z`,
    ...overrides,
  };
}
