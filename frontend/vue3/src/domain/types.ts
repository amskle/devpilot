export type TaskStatus =
  | "CREATED"
  | "RUNNING"
  | "WAITING_RISK_APPROVAL"
  | "WAITING_HUMAN_INTERVENTION"
  | "CANCELLING"
  | "CANCELLED"
  | "COMPLETED"
  | "COMPLETED_NO_CHANGES"
  | "FAILED"
  | "POLICY_REJECTED";

export interface ArtifactRef {
  artifact_id?: string;
  sha256?: string;
  kind?: string;
  [key: string]: unknown;
}

export interface ExecutionBudget {
  max_iterations: number;
  max_plan_revisions: number;
  max_rollbacks: number;
  max_llm_calls: number;
  max_tool_calls: number;
  max_total_tokens: number;
  max_cost: string | null;
  cost_currency: string;
  max_active_seconds: number;
  iterations_used: number;
  plan_revisions_used: number;
  rollbacks_used: number;
  llm_calls_used: number;
  tool_calls_used: number;
  prompt_tokens_used: number;
  completion_tokens_used: number;
  cost_used: string;
  active_seconds_used: number;
  pricing_snapshot_ref?: string | null;
}

export interface ApprovalRequest {
  approval_id: string;
  task_id: string;
  run_id: string;
  patch_ref: ArtifactRef;
  patch_hash: string;
  base_revision: string;
  risk_report_ref: ArtifactRef;
  requested_at: string;
  expires_at: string;
}

export interface PatchProposal {
  patch_id: string;
  patch_ref: ArtifactRef;
  patch_hash: string;
  base_revision: string;
  changed_files: string[];
  summary: string;
  status: string;
}

export interface FailureRecord {
  failure_id: string;
  category: string;
  error_code: string;
  summary: string;
  recovery_action: string;
  related_files: string[];
  occurred_at: string;
}

export interface RecoveryPoint {
  recovery_point_id: string;
  checkpoint_id: string;
  workspace_id: string;
  repository_snapshot_id: string;
  plan_id: string;
  plan_version: number;
  state_revision: number;
  created_at: string;
}

export interface VerificationResult {
  passed?: boolean;
  exit_code?: number;
  command?: string;
  summary?: string;
  failed_test_ids?: string[];
  artifact_refs?: ArtifactRef[];
  [key: string]: unknown;
}

export interface TaskState {
  schema_version: number;
  state_revision: number;
  task_id: string;
  run_id: string;
  parent_run_id: string | null;
  status: TaskStatus;
  pause_reason: string | null;
  current_node: string;
  active_plan_ref: ArtifactRef | null;
  diagnosis: Record<string, unknown> | null;
  patch_proposal: PatchProposal | null;
  verification: VerificationResult | null;
  execution_budget: ExecutionBudget;
  pending_approval: ApprovalRequest | null;
  pending_replan_request: Record<string, unknown> | null;
  latest_failure: FailureRecord | null;
  active_recovery_point_ref: string | null;
  model_profile?: { provider: string; model: string } | null;
  request?: string;
  updated_at?: string;
}

export interface TaskSummary {
  task_id: string;
  run_id: string;
  status: TaskStatus;
  current_node: string;
  state_revision: number;
  pause_reason: string | null;
  request?: string;
  model?: string;
  updated_at?: string;
  execution_budget?: ExecutionBudget;
  verification?: VerificationResult | null;
}

export interface PlanDocument {
  plan_id: string;
  version: number;
  parent_version: number | null;
  status?: "ACTIVE" | "SUPERSEDED";
  created_at: string;
  change_reason: string | null;
  summary: string;
  tasks: Array<Record<string, unknown>>;
  acceptance_criteria: string[];
  risks: string[];
  content_hash: string;
}

export interface ExecutionEvent {
  event_id: string;
  schema_version: 1;
  task_id: string;
  run_id: string;
  state_revision: number | null;
  node_name: string | null;
  attempt: number | null;
  event_type: string;
  sequence_number: number;
  correlation_id: string | null;
  causation_id: string | null;
  payload: Record<string, unknown>;
  artifact_refs: Array<ArtifactRef | string>;
  checkpoint_confirmed: boolean;
  created_at: string;
}

export interface TraceView {
  task_id: string;
  run_id: string | null;
  event_count: number;
  first_sequence: number | null;
  last_sequence: number | null;
  sequence_gaps: number[];
  events: ExecutionEvent[];
}

export interface DiffDocument {
  patch_id?: string;
  text: string;
  changed_files: string[];
  patch_hash?: string;
}

export interface Message {
  message_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}
