<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { api, ApiError } from "@/api/client";
import BrowseDialog from "@/components/BrowseDialog.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import { compactId, formatDate } from "@/domain/format";
import type { TaskSummary } from "@/domain/types";

defineProps<{ currentTaskId?: string }>();
const emit = defineEmits<{ close: [] }>();
const query = ref("");
const activeQuery = ref("");
const tasks = ref<TaskSummary[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const nextCursor = ref<string | null>(null);
const cursors = ref<(string | undefined)[]>([undefined]);
const page = ref(0);
let requestId = 0;

async function load(targetPage = 0, search = activeQuery.value): Promise<void> {
  const id = ++requestId;
  loading.value = true;
  error.value = null;
  try {
    const result = await api.listTasks({ limit: 10, query: search, cursor: targetPage ? cursors.value[targetPage] : undefined });
    if (id !== requestId) return;
    if (!targetPage) cursors.value = [undefined];
    tasks.value = result.items;
    nextCursor.value = result.next_cursor;
    activeQuery.value = search;
    page.value = targetPage;
  } catch (caught) {
    if (id === requestId) error.value = caught instanceof ApiError ? caught.message : "无法查询任务，请重试。";
  } finally {
    if (id === requestId) loading.value = false;
  }
}

function next(): void {
  if (!nextCursor.value) return;
  cursors.value[page.value + 1] = nextCursor.value;
  void load(page.value + 1);
}

onMounted(() => load());
onBeforeUnmount(() => { requestId += 1; });
</script>

<template>
  <BrowseDialog title="查询任务历史" @close="emit('close')">
    <form class="history-query" @submit.prevent="load(0, query.trim())">
      <label class="field"><span>搜索任务</span><input v-model="query" autofocus type="search" maxlength="200" placeholder="输入需求或任务 ID" /></label>
      <button class="button button-primary" type="submit">查询</button>
    </form>
    <div class="browse-results" aria-live="polite" :aria-busy="loading">
      <p v-if="loading" class="sidebar-empty">正在查询…</p>
      <div v-else-if="error" class="sidebar-error" role="alert"><span>{{ error }}</span><button type="button" @click="load(0, query.trim())">重试</button></div>
      <template v-else>
        <RouterLink v-for="task in tasks" :key="task.task_id" class="history-item" :class="{ active: task.task_id === currentTaskId }" :to="`/tasks/${task.task_id}`" @click="emit('close')">
          <div><strong>{{ task.request || compactId(task.task_id) }}</strong><small>{{ task.task_id }}</small><time>{{ formatDate(task.updated_at) }}</time></div>
          <StatusBadge :status="task.status" />
        </RouterLink>
        <p v-if="!tasks.length" class="sidebar-empty">没有匹配的任务。</p>
      </template>
    </div>
    <footer class="modal-actions">
      <span>第 {{ page + 1 }} 页</span>
      <button class="button button-secondary" type="button" :disabled="loading || page === 0" @click="load(page - 1)">上一页</button>
      <button class="button button-secondary" type="button" :disabled="loading || !nextCursor" @click="next">下一页</button>
    </footer>
  </BrowseDialog>
</template>
