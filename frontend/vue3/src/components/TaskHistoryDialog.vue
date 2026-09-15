<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api, ApiError } from "@/api/client";
import BrowseDialog from "@/components/BrowseDialog.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import { compactId, formatDate } from "@/domain/format";
import type { TaskSummary } from "@/domain/types";

defineProps<{ currentTaskId?: string }>();
const emit = defineEmits<{ close: []; deleted: [taskIds: string[]] }>();
const query = ref("");
const activeQuery = ref("");
const tasks = ref<TaskSummary[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const nextCursor = ref<string | null>(null);
const cursors = ref<(string | undefined)[]>([undefined]);
const page = ref(0);
const deleting = ref<string | null>(null);
const deletingBatch = ref(false);
const selected = ref(new Set<string>());
let requestId = 0;
const terminalStatuses = new Set(["COMPLETED", "COMPLETED_NO_CHANGES", "CANCELLED", "FAILED", "POLICY_REJECTED"]);
const selectableTasks = computed(() => tasks.value.filter((task) => terminalStatuses.has(task.status)));
const allPageSelected = computed(() => selectableTasks.value.length > 0
  && selectableTasks.value.every((task) => selected.value.has(task.task_id)));

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

function search(): void {
  selected.value = new Set();
  void load(0, query.value.trim());
}

function toggleTask(taskId: string): void {
  const updated = new Set(selected.value);
  if (updated.has(taskId)) updated.delete(taskId);
  else updated.add(taskId);
  selected.value = updated;
}

function togglePage(): void {
  const updated = new Set(selected.value);
  for (const task of selectableTasks.value) {
    if (allPageSelected.value) updated.delete(task.task_id);
    else updated.add(task.task_id);
  }
  selected.value = updated;
}

async function remove(task: TaskSummary): Promise<void> {
  if (!window.confirm(`确定永久删除“${task.request || task.task_id}”及其全部运行数据吗？`)) return;
  deleting.value = task.task_id;
  error.value = null;
  try {
    await api.deleteTask(task.task_id);
    tasks.value = tasks.value.filter((item) => item.task_id !== task.task_id);
    const updated = new Set(selected.value);
    updated.delete(task.task_id);
    selected.value = updated;
    emit("deleted", [task.task_id]);
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "无法删除任务，请重试。";
  } finally {
    deleting.value = null;
  }
}

async function removeSelected(): Promise<void> {
  const taskIds = [...selected.value];
  if (!taskIds.length) return;
  if (!window.confirm(`确定永久删除选中的 ${taskIds.length} 个任务及其全部运行数据吗？`)) return;
  deletingBatch.value = true;
  error.value = null;
  try {
    const result = await api.deleteTasks(taskIds);
    const deleted = new Set(result.deleted_task_ids);
    tasks.value = tasks.value.filter((task) => !deleted.has(task.task_id));
    selected.value = new Set();
    emit("deleted", result.deleted_task_ids);
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "无法批量删除任务，请重试。";
  } finally {
    deletingBatch.value = false;
  }
}

onMounted(() => load());
onBeforeUnmount(() => { requestId += 1; });
</script>

<template>
  <BrowseDialog title="查询任务历史" @close="emit('close')">
    <form class="history-query" @submit.prevent="search">
      <label class="field"><span>搜索任务</span><input v-model="query" autofocus type="search" maxlength="200" placeholder="输入需求或任务 ID" /></label>
      <button class="button button-primary" type="submit">查询</button>
    </form>
    <div class="history-batch-toolbar">
      <label>
        <input type="checkbox" :checked="allPageSelected" :disabled="!selectableTasks.length || deletingBatch" @change="togglePage" />
        全选本页可删除任务
      </label>
      <button class="button button-danger-ghost" type="button" :disabled="!selected.size || deletingBatch" @click="removeSelected">
        {{ deletingBatch ? "正在删除…" : `删除所选（${selected.size}）` }}
      </button>
    </div>
    <div class="browse-results" aria-live="polite" :aria-busy="loading">
      <p v-if="loading" class="sidebar-empty">正在查询…</p>
      <div v-else-if="error" class="sidebar-error" role="alert"><span>{{ error }}</span><button type="button" @click="load(0, query.trim())">重试</button></div>
      <template v-else>
        <div v-for="task in tasks" :key="task.task_id" class="history-row">
          <input
            v-if="terminalStatuses.has(task.status)"
            type="checkbox"
            :checked="selected.has(task.task_id)"
            :disabled="deletingBatch"
            :aria-label="`选择任务 ${task.request || task.task_id}`"
            @change="toggleTask(task.task_id)"
          />
          <span v-else class="history-select-placeholder" aria-hidden="true" />
          <RouterLink class="history-item" :class="{ active: task.task_id === currentTaskId }" :to="`/tasks/${task.task_id}`" @click="emit('close')">
            <div><strong>{{ task.request || compactId(task.task_id) }}</strong><small>{{ task.task_id }}</small><time>{{ formatDate(task.updated_at) }}</time></div>
            <StatusBadge :status="task.status" />
          </RouterLink>
          <button
            v-if="terminalStatuses.has(task.status)"
            class="icon-button history-delete"
            type="button"
            :disabled="deleting === task.task_id || deletingBatch"
            :aria-label="`删除任务 ${task.request || task.task_id}`"
            title="永久删除任务历史"
            @click="remove(task)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5" /></svg>
          </button>
        </div>
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
