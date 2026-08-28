import { ApiClient, ApiError } from "./client";
import type { ExecutionEvent } from "@/domain/types";

type EventListener = (events: ExecutionEvent[]) => void;
type StateListener = (state: "connecting" | "connected" | "recovering" | "closed") => void;

export interface EventStreamOptions {
  api: ApiClient;
  taskId: string;
  runId: string;
  onEvents: EventListener;
  onState?: StateListener;
  websocketFactory?: (url: string) => WebSocket;
  reconnectDelay?: (attempt: number) => number;
}

function wsBase(httpBase: string): string {
  if (/^https?:\/\//.test(httpBase)) return httpBase.replace(/^http/, "ws");
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${httpBase.startsWith("/") ? "" : "/"}${httpBase}`;
}

export class TaskEventStream {
  private socket: WebSocket | null = null;
  private stopped = true;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempt = 0;
  private cursor = 0;
  private readonly seen = new Set<string>();
  private runId: string;

  constructor(private readonly options: EventStreamOptions) {
    this.runId = options.runId;
  }

  get lastSequence(): number {
    return this.cursor;
  }

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.options.onState?.("closed");
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.disconnectSocket();
  }

  async switchRun(runId: string): Promise<void> {
    if (runId === this.runId) return;
    this.disconnectSocket();
    this.runId = runId;
    this.cursor = 0;
    this.seen.clear();
    this.reconnectAttempt = 0;
    if (!this.stopped) await this.connect();
  }

  private ingest(incoming: ExecutionEvent[]): void {
    const accepted = incoming
      .filter((event) => event.run_id === this.runId && !this.seen.has(event.event_id))
      .sort((left, right) => left.sequence_number - right.sequence_number);
    if (!accepted.length) return;
    for (const event of accepted) {
      this.seen.add(event.event_id);
      this.cursor = Math.max(this.cursor, event.sequence_number);
    }
    this.options.onEvents(accepted);
  }

  private async catchUp(): Promise<void> {
    this.options.onState?.("recovering");
    while (!this.stopped) {
      const previousCursor = this.cursor;
      const events = await this.options.api.getEvents(this.options.taskId, this.runId, this.cursor);
      this.ingest(events);
      if (events.length < 200 || this.cursor === previousCursor) return;
    }
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.options.onState?.("connecting");
    try {
      await this.catchUp();
      const { ticket } = await this.options.api.createEventTicket(this.options.taskId);
      if (this.stopped) return;
      const query = new URLSearchParams({ run_id: this.runId, ticket, after_sequence: String(this.cursor) });
      const url = `${wsBase(this.options.api.baseUrl)}/tasks/${encodeURIComponent(this.options.taskId)}/events?${query}`;
      const createSocket = this.options.websocketFactory ?? ((target: string) => new WebSocket(target));
      this.socket = createSocket(url);
      this.socket.onopen = () => {
        this.reconnectAttempt = 0;
        this.options.onState?.("connected");
      };
      this.socket.onmessage = (message) => {
        try {
          const parsed = JSON.parse(String(message.data)) as ExecutionEvent | { event: ExecutionEvent };
          const event = "event" in parsed ? parsed.event : parsed;
          if (event.run_id === this.runId && event.sequence_number > this.cursor + 1) {
            this.disconnectSocket();
            this.scheduleReconnect();
            return;
          }
          this.ingest([event]);
        } catch {
          // Malformed live copies are ignored; cursor recovery remains authoritative.
        }
      };
      this.socket.onclose = () => this.scheduleReconnect();
      this.socket.onerror = () => this.socket?.close();
    } catch (error) {
      const terminalHttpError =
        error instanceof ApiError && [401, 403, 404].includes(error.status);
      if (terminalHttpError) this.options.onState?.("closed");
      else this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.retryTimer) return;
    this.options.onState?.("recovering");
    const delay = this.options.reconnectDelay?.(this.reconnectAttempt) ?? Math.min(30_000, 1_000 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.connect();
    }, delay);
  }

  private disconnectSocket(): void {
    if (!this.socket) return;
    this.socket.onclose = null;
    this.socket.onerror = null;
    this.socket.close();
    this.socket = null;
  }
}
