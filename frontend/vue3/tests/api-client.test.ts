import { describe, expect, it, vi } from "vitest";
import { AUTH_REQUIRED_EVENT, ApiClient, ApiError } from "@/api/client";
import { waitingState } from "./fixtures";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("ApiClient control contract", () => {
  it("notifies the application when the API requires a bearer token", async () => {
    const listener = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, listener);
    const client = new ApiClient({
      fetchImpl: vi.fn(async () => response({ detail: "Bearer token required" }, 401)) as typeof fetch,
      tokenProvider: () => null,
    });

    await expect(client.listTasks()).rejects.toMatchObject({
      status: 401,
      message: "Bearer token required",
    });

    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({
      message: "Bearer token required",
    });
    window.removeEventListener(AUTH_REQUIRED_EVENT, listener);
  });

  it("binds approval target, revision, bearer token and idempotency key", async () => {
    const calls: RequestInit[] = [];
    const fetchImpl = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      calls.push(init ?? {});
      return response(waitingState);
    });
    const client = new ApiClient({ baseUrl: "https://api.example.test/api", fetchImpl: fetchImpl as typeof fetch, tokenProvider: () => "secret" });
    const payload = {
      approval_id: "approval_123456789",
      patch_hash: "b".repeat(64),
      base_revision: "c".repeat(40),
      expected_state_revision: 7,
    };

    await client.approve("task_1", payload);

    const headers = calls[0].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer secret");
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(calls[0].body))).toEqual(payload);
    expect(fetchImpl).toHaveBeenCalledWith("https://api.example.test/api/tasks/task_1/approve", expect.objectContaining({ method: "POST" }));
  });

  it("reuses an idempotency key after an ambiguous network failure", async () => {
    const keys: string[] = [];
    const fetchImpl = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      keys.push(new Headers(init?.headers).get("Idempotency-Key") ?? "");
      if (keys.length === 1) throw new TypeError("offline");
      return response(waitingState);
    });
    const client = new ApiClient({ baseUrl: "/api", fetchImpl: fetchImpl as typeof fetch });
    const payload = { expected_state_revision: 7 };

    await expect(client.cancel("task_1", payload)).rejects.toMatchObject({ status: 0 });
    await client.cancel("task_1", payload);

    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("exposes revision conflicts for refresh-and-retry handling", async () => {
    const client = new ApiClient({
      fetchImpl: vi.fn(async () => response({ detail: "expected revision 7, actual 8" }, 409)) as typeof fetch,
    });

    const operation = client.cancel("task_1", { expected_state_revision: 7 });
    await expect(operation).rejects.toBeInstanceOf(ApiError);
    await expect(operation).rejects.toMatchObject({ status: 409, isConflict: true });
  });

  it("persists ordinary messages independently with an idempotency key", async () => {
    const created = { message_id: "message_1", role: "user", content: "Add context", created_at: "2026-08-27T00:00:00Z" };
    const fetchImpl = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) => response(created));
    const client = new ApiClient({ baseUrl: "/api", fetchImpl: fetchImpl as typeof fetch });

    await expect(client.sendMessage("task_1", { content: "Add context" })).resolves.toEqual(created);

    expect(fetchImpl).toHaveBeenCalledWith("/api/tasks/task_1/messages", expect.objectContaining({ method: "POST" }));
    const init = fetchImpl.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({ content: "Add context" });
  });
});
