import { onBeforeUnmount, ref, watch, type Ref } from "vue";

import { TaskEventStream } from "@/api/events";
import { api } from "@/api/client";
import type { ExecutionEvent } from "@/domain/types";

export function useTaskEvents(taskId: Ref<string>, runId: Ref<string>) {
  const events = ref<ExecutionEvent[]>([]);
  const connection = ref<"connecting" | "connected" | "recovering" | "closed">("closed");
  let stream: TaskEventStream | null = null;

  function merge(incoming: ExecutionEvent[]): void {
    const byId = new Map(events.value.map((event) => [event.event_id, event]));
    for (const event of incoming) byId.set(event.event_id, event);
    events.value = [...byId.values()].sort((left, right) => left.sequence_number - right.sequence_number);
  }

  async function restart(): Promise<void> {
    stream?.stop();
    events.value = [];
    if (!taskId.value || !runId.value) return;
    stream = new TaskEventStream({
      api,
      taskId: taskId.value,
      runId: runId.value,
      onEvents: merge,
      onState: (state) => (connection.value = state),
    });
    await stream.start();
  }

  watch([taskId, runId], () => void restart(), { immediate: true });
  onBeforeUnmount(() => stream?.stop());

  return { events, connection, restart };
}
