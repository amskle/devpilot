<script setup lang="ts">
import { computed } from "vue";
import { compactId, formatDate } from "@/domain/format";
import type { ExecutionEvent } from "@/domain/types";

const props = defineProps<{
  events: ExecutionEvent[];
  connection: "connecting" | "connected" | "recovering" | "closed";
}>();

const connectionLabel = computed(() => ({
  connecting: "正在连接",
  connected: "实时同步",
  recovering: "游标补拉",
  closed: "已断开",
})[props.connection]);

function eventTitle(event: ExecutionEvent): string {
  const summary = event.payload.agent_summary ?? event.payload.summary;
  return typeof summary === "string" ? summary : event.event_type.replaceAll("_", " ");
}
</script>

<template>
  <section class="panel timeline-panel">
    <header class="panel-header timeline-heading">
      <div><span class="eyebrow">DURABLE EVENT STREAM</span><h2>执行时间线</h2></div>
      <span class="connection-state" :class="connection"><i />{{ connectionLabel }}</span>
    </header>
    <div v-if="events.length" class="timeline-list">
      <article v-for="event in [...events].reverse()" :key="event.event_id" class="timeline-event">
        <div class="timeline-sequence">{{ String(event.sequence_number).padStart(3, "0") }}</div>
        <div class="timeline-rail"><i /></div>
        <div class="timeline-content">
          <div class="event-meta">
            <span>{{ event.node_name ?? "control" }}</span>
            <time>{{ formatDate(event.created_at) }}</time>
          </div>
          <strong>{{ eventTitle(event) }}</strong>
          <div class="event-foot">
            <code>{{ event.event_type }}</code>
            <span v-if="event.state_revision !== null">revision {{ event.state_revision }}</span>
            <span :class="event.checkpoint_confirmed ? 'confirmed' : 'pending'">
              {{ event.checkpoint_confirmed ? "checkpoint 已确认" : event.state_revision === null ? "独立事件" : "等待 checkpoint" }}
            </span>
            <span :title="event.event_id">{{ compactId(event.event_id) }}</span>
          </div>
        </div>
      </article>
    </div>
    <div v-else class="empty-state compact"><strong>等待事件</strong><span>连接建立后会按持久化游标自动补拉。</span></div>
  </section>
</template>
