<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api, ApiError } from "@/api/client";
import StatusBadge from "@/components/StatusBadge.vue";
import { compactId, formatDate } from "@/domain/format";
import type { TaskSummary } from "@/domain/types";

const props = defineProps<{ currentTaskId?: string }>();

const tasks = ref<TaskSummary[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const query = ref("");
const open = ref(false);

const terminalStatuses = new Set(["COMPLETED", "COMPLETED_NO_CHANGES", "CANCELLED", "FAILED", "POLICY_REJECTED"]);
const filtered = computed(() => {
  const term = query.value.trim().toLocaleLowerCase();
  if (!term) return tasks.value;
  return tasks.value.filter((task) => `${task.request ?? ""} ${task.task_id}`.toLocaleLowerCase().includes(term));
});
const metrics = computed(() => ({
  total: tasks.value.length,
  running: tasks.value.filter((task) => !terminalStatuses.has(task.status)).length,
  attention: tasks.value.filter((task) => ["WAITING_RISK_APPROVAL", "WAITING_HUMAN_INTERVENTION"].includes(task.status)).length,
  completed: tasks.value.filter((task) => ["COMPLETED", "COMPLETED_NO_CHANGES"].includes(task.status)).length,
  tokens: tasks.value.reduce((total, task) => {
    const budget = task.execution_budget;
    return total + (budget ? budget.prompt_tokens_used + budget.completion_tokens_used : 0);
  }, 0),
}));

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    tasks.value = (await api.listTasks({ limit: 100 })).items;
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "无法读取任务历史";
  } finally {
    loading.value = false;
  }
}

watch(() => props.currentTaskId, () => (open.value = false));
onMounted(load);
</script>

<template>
  <button class="sidebar-trigger" type="button" :aria-expanded="open" aria-controls="task-sidebar" @click="open = true">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" /></svg>
    <span>任务历史</span>
  </button>
  <button v-if="open" class="sidebar-backdrop" type="button" aria-label="关闭任务历史" @click="open = false" />
  <aside id="task-sidebar" class="task-sidebar" :class="{ open }" aria-label="任务历史">
    <div class="sidebar-head">
      <div>
        <strong>任务历史</strong>
        <span>{{ metrics.total }} 个任务</span>
      </div>
      <button class="icon-button sidebar-close" type="button" aria-label="关闭任务历史" @click="open = false">×</button>
    </div>

    <RouterLink class="button button-primary sidebar-new" to="/">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      新建任务
    </RouterLink>

    <label class="sidebar-search">
      <span>搜索任务</span>
      <div><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></svg><input v-model="query" type="search" placeholder="需求或任务 ID" /></div>
    </label>

    <details class="metrics-disclosure">
      <summary><span>运行概览</span><small>展开指标</small></summary>
      <dl>
        <div><dt>任务总数</dt><dd>{{ metrics.total }}</dd></div>
        <div><dt>进行中</dt><dd>{{ metrics.running }}</dd></div>
        <div><dt>需要处理</dt><dd>{{ metrics.attention }}</dd></div>
        <div><dt>已完成</dt><dd>{{ metrics.completed }}</dd></div>
        <div class="metric-wide"><dt>Token 消耗</dt><dd>{{ metrics.tokens.toLocaleString() }}</dd></div>
      </dl>
    </details>

    <div class="history-list" aria-live="polite">
      <div v-if="loading" class="sidebar-skeleton" aria-label="正在读取任务历史"><i /><i /><i /></div>
      <div v-else-if="error" class="sidebar-error" role="alert"><span>{{ error }}</span><button type="button" @click="load">重试</button></div>
      <template v-else>
        <RouterLink
          v-for="task in filtered"
          :key="task.task_id"
          class="history-item"
          :class="{ active: task.task_id === currentTaskId }"
          :to="`/tasks/${task.task_id}`"
        >
          <div><strong>{{ task.request || compactId(task.task_id) }}</strong><time>{{ formatDate(task.updated_at) }}</time></div>
          <StatusBadge :status="task.status" />
        </RouterLink>
      </template>
      <div v-if="!loading && !error && !filtered.length" class="sidebar-empty">没有匹配的任务。</div>
    </div>

    <footer class="sidebar-foot"><i aria-hidden="true" /><span>事件持久化 · 控制命令显式执行</span></footer>
  </aside>
</template>
