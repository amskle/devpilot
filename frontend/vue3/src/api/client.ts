import type {
  DiffDocument,
  ExecutionEvent,
  Message,
  PlanDocument,
  RecoveryPoint,
  TaskState,
  TaskSummary,
  TraceView,
} from "@/domain/types";

const DEFAULT_BASE_URL = "/api";
const TOKEN_KEY = "devpilot.access-token";
const IDEMPOTENCY_PREFIX = "devpilot.idempotency.";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  tokenProvider?: () => string | null;
}

export interface TaskListResult {
  items: TaskSummary[];
  next_cursor: string | null;
}

export interface ControlTarget {
  expected_state_revision: number;
}

function browserStorage(): Storage | null {
  return typeof window === "undefined" ? null : window.sessionStorage;
}

export function setAccessToken(token: string): void {
  const storage = browserStorage();
  if (!storage) return;
  const normalized = token.trim();
  if (normalized) storage.setItem(TOKEN_KEY, normalized);
  else storage.removeItem(TOKEN_KEY);
}

export function getAccessToken(): string | null {
  return browserStorage()?.getItem(TOKEN_KEY) ?? null;
}

function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function fingerprint(value: object): string {
  const text = JSON.stringify(value);
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function stableKey(taskId: string, operation: string, target: object): string {
  return `${IDEMPOTENCY_PREFIX}${taskId}.${operation}.${fingerprint(target)}`;
}

function idempotencyKey(taskId: string, operation: string, target: object): string {
  const storage = browserStorage();
  const key = stableKey(taskId, operation, target);
  const existing = storage?.getItem(key);
  if (existing) return existing;
  const created = randomId();
  storage?.setItem(key, created);
  return created;
}

function releaseIdempotencyKey(taskId: string, operation: string, target: object): void {
  browserStorage()?.removeItem(stableKey(taskId, operation, target));
}

function messageFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const candidate = body as Record<string, unknown>;
    if (typeof candidate.detail === "string") return candidate.detail;
    if (typeof candidate.message === "string") return candidate.message;
  }
  return fallback;
}

export class ApiClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly tokenProvider: () => string | null;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? import.meta.env.VITE_API_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
    this.tokenProvider = options.tokenProvider ?? getAccessToken;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    const token = this.tokenProvider();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      throw new ApiError(error instanceof Error ? error.message : "网络连接失败", 0, error);
    }
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!response.ok) {
      throw new ApiError(messageFromBody(body, `请求失败 (${response.status})`), response.status, body);
    }
    return body as T;
  }

  async listTasks(query: { status?: string; cursor?: string; limit?: number } = {}): Promise<TaskListResult> {
    const search = new URLSearchParams();
    if (query.status) search.set("status", query.status);
    if (query.cursor) search.set("cursor", query.cursor);
    search.set("limit", String(query.limit ?? 100));
    const body = await this.request<TaskListResult | TaskSummary[]>(`/tasks?${search}`);
    return Array.isArray(body) ? { items: body, next_cursor: null } : body;
  }

  getTask(taskId: string): Promise<TaskState> {
    return this.request<TaskState>(`/tasks/${encodeURIComponent(taskId)}`);
  }

  createTask(input: { repo: string; request: string; revision?: string; model?: string }): Promise<TaskState> {
    return this.request<TaskState>("/tasks", { method: "POST", body: JSON.stringify(input) });
  }

  getPlan(taskId: string): Promise<PlanDocument | PlanDocument[]> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/plan`);
  }

  getDiff(taskId: string): Promise<DiffDocument> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/diff`);
  }

  getTrace(taskId: string, runId?: string): Promise<TraceView> {
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return this.request(`/tasks/${encodeURIComponent(taskId)}/trace${query}`);
  }

  getMessages(taskId: string): Promise<Message[]> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/messages`);
  }

  getRecoveryPoints(taskId: string): Promise<RecoveryPoint[]> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/recovery-points`);
  }

  getEvents(taskId: string, runId: string, afterSequence: number, limit = 200): Promise<ExecutionEvent[]> {
    const query = new URLSearchParams({
      run_id: runId,
      after_sequence: String(afterSequence),
      limit: String(limit),
    });
    return this.request(`/tasks/${encodeURIComponent(taskId)}/events?${query}`);
  }

  createEventTicket(taskId: string): Promise<{ ticket: string; expires_at: string }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/event-ticket`, { method: "POST" });
  }

  private async control<T extends object>(taskId: string, operation: string, payload: T): Promise<TaskState> {
    const key = idempotencyKey(taskId, operation, payload);
    try {
      const result = await this.request<TaskState>(`/tasks/${encodeURIComponent(taskId)}/${operation}`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(payload),
      });
      releaseIdempotencyKey(taskId, operation, payload);
      return result;
    } catch (error) {
      if (error instanceof ApiError && error.status !== 0) {
        releaseIdempotencyKey(taskId, operation, payload);
      }
      throw error;
    }
  }

  approve(taskId: string, payload: ControlTarget & {
    approval_id: string;
    patch_hash: string;
    base_revision: string;
  }): Promise<TaskState> {
    return this.control(taskId, "approve", payload);
  }

  reject(taskId: string, payload: ControlTarget & {
    approval_id: string;
    patch_hash: string;
    base_revision: string;
  }): Promise<TaskState> {
    return this.control(taskId, "reject", payload);
  }

  cancel(taskId: string, payload: ControlTarget): Promise<TaskState> {
    return this.control(taskId, "cancel", payload);
  }

  rollback(taskId: string, payload: ControlTarget & { recovery_point_id: string }): Promise<TaskState> {
    return this.control(taskId, "rollback", payload);
  }

  restore(taskId: string, payload: ControlTarget & { recovery_point_id: string }): Promise<TaskState> {
    return this.control(taskId, "restore", payload);
  }

  changeRequest(taskId: string, payload: ControlTarget & {
    content: string;
    confirm_patch_invalidation: boolean;
  }): Promise<TaskState> {
    return this.control(taskId, "change-requests", payload);
  }
}

export const api = new ApiClient();
