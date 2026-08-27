<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, ApiError } from "@/api/client";
import StatusBadge from "@/components/StatusBadge.vue";
import { compactId, formatDate } from "@/domain/format";
import type { TaskState, TaskSummary } from "@/domain/types";

const tasks = ref<TaskSummary[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const status = ref("");
const showCreate = ref(false);
const creating = ref(false);
const form = ref({ repo: "", request: "", revision: "HEAD", model: "" });

const metrics = computed(() => {
  const total = tasks.value.length;
  const terminal = tasks.value.filter((task) => ["COMPLETED", "COMPLETED_NO_CHANGES", "CANCELLED", "FAILED", "POLICY_REJECTED"].includes(task.status));
  const completed = tasks.value.filter((task) => ["COMPLETED", "COMPLETED_NO_CHANGES"].includes(task.status)).length;
  const intervention = tasks.value.filter((task) => task.status === "WAITING_HUMAN_INTERVENTION").length;
  const approvals = tasks.value.filter((task) => task.status === "WAITING_RISK_APPROVAL").length;
  const verified = tasks.value.filter((task) => task.verification?.passed !== undefined);
  const budgets = tasks.value.flatMap((task) => task.execution_budget ? [task.execution_budget] : []);
  const tokens = budgets.reduce((sum, budget) => sum + budget.prompt_tokens_used + budget.completion_tokens_used, 0);
  const cost = budgets.reduce((sum, budget) => sum + Number.parseFloat(budget.cost_used || "0"), 0);
  const rollbacks = budgets.reduce((sum, budget) => sum + budget.rollbacks_used, 0);
  const averageIterations = budgets.length ? budgets.reduce((sum, budget) => sum + budget.iterations_used, 0) / budgets.length : 0;
  const averageSeconds = budgets.length ? budgets.reduce((sum, budget) => sum + budget.active_seconds_used, 0) / budgets.length : 0;
  return {
    total,
    completed,
    intervention,
    approvals,
    success: terminal.length ? Math.round((completed / terminal.length) * 100) : null,
    verificationRate: verified.length ? Math.round((verified.filter((task) => task.verification?.passed).length / verified.length) * 100) : null,
    averageIterations,
    averageSeconds,
    tokens,
    cost,
    rollbacks,
  };
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    tasks.value = (await api.listTasks({ status: status.value || undefined })).items;
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "无法读取任务列表";
  } finally {
    loading.value = false;
  }
}

async function create(): Promise<void> {
  creating.value = true;
  error.value = null;
  try {
    const result: TaskState = await api.createTask({ ...form.value, model: form.value.model || undefined });
    showCreate.value = false;
    await load();
    window.location.assign(`/tasks/${result.task_id}`);
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "任务创建失败";
  } finally {
    creating.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page dashboard-page">
    <section class="hero-row">
      <div><span class="eyebrow">ENGINEERING OPERATIONS</span><h1>任务控制台</h1><p>从计划到验证，持续掌握每一次安全代码变更。</p></div>
      <button class="button button-primary" type="button" @click="showCreate = !showCreate">{{ showCreate ? "收起" : "+ 创建任务" }}</button>
    </section>

    <form v-if="showCreate" class="panel create-form" @submit.prevent="create">
      <label class="field"><span>仓库路径</span><input v-model="form.repo" required placeholder="C:\projects\example" /></label>
      <label class="field wide"><span>任务需求</span><input v-model="form.request" required minlength="5" placeholder="描述需要诊断和修复的问题" /></label>
      <label class="field"><span>Git revision</span><input v-model="form.revision" required /></label>
      <label class="field"><span>模型（可选）</span><input v-model="form.model" placeholder="使用服务默认模型" /></label>
      <button class="button button-primary" type="submit" :disabled="creating">{{ creating ? "正在创建…" : "启动隔离任务" }}</button>
    </form>

    <p v-if="error" class="page-error" role="alert">{{ error }} <button type="button" @click="load">重试</button></p>

    <section class="metric-grid" aria-label="任务指标">
      <article><span>任务总数</span><strong>{{ metrics.total }}</strong><small>当前查询范围</small></article>
      <article><span>成功率</span><strong>{{ metrics.success ?? "—" }}<i v-if="metrics.success !== null">%</i></strong><small>{{ metrics.completed }} 个已完成终态</small></article>
      <article><span>验证通过率</span><strong>{{ metrics.verificationRate ?? "—" }}<i v-if="metrics.verificationRate !== null">%</i></strong><small>基于结构化验证报告</small></article>
      <article><span>平均迭代</span><strong>{{ metrics.averageIterations.toFixed(1) }}</strong><small>平均活跃 {{ Math.round(metrics.averageSeconds) }} 秒</small></article>
      <article><span>待审批</span><strong>{{ metrics.approvals }}</strong><small>需要明确决策</small></article>
      <article><span>人工介入率</span><strong>{{ metrics.total ? Math.round(metrics.intervention / metrics.total * 100) : 0 }}<i>%</i></strong><small>{{ metrics.intervention }} 个已安全停止</small></article>
      <article><span>Token 用量</span><strong>{{ metrics.tokens.toLocaleString() }}</strong><small>Prompt + Completion</small></article>
      <article><span>累计费用</span><strong><i>$</i>{{ metrics.cost.toFixed(4) }}</strong><small>{{ metrics.rollbacks }} 次回滚</small></article>
    </section>

    <section class="panel task-table-panel">
      <header class="panel-header">
        <div><span class="eyebrow">TASK FLEET</span><h2>最近任务</h2></div>
        <label class="filter-select"><span>状态</span><select v-model="status" @change="load"><option value="">全部</option><option value="RUNNING">执行中</option><option value="WAITING_RISK_APPROVAL">待审批</option><option value="WAITING_HUMAN_INTERVENTION">人工介入</option><option value="COMPLETED">已完成</option><option value="FAILED">失败</option></select></label>
      </header>
      <div v-if="loading" class="loading-state">正在读取任务状态…</div>
      <div v-else-if="tasks.length" class="task-table">
        <RouterLink v-for="task in tasks" :key="task.task_id" class="task-row" :to="`/tasks/${task.task_id}`">
          <div class="task-main"><strong>{{ task.request || compactId(task.task_id) }}</strong><span>{{ compactId(task.task_id) }} · revision {{ task.state_revision }}</span></div>
          <StatusBadge :status="task.status" />
          <div class="task-node"><small>CURRENT NODE</small><span>{{ task.current_node.replaceAll("_", " ") }}</span></div>
          <div class="task-model"><small>MODEL</small><span>{{ task.model || "默认模型" }}</span></div>
          <time>{{ formatDate(task.updated_at) }}</time><b aria-hidden="true">→</b>
        </RouterLink>
      </div>
      <div v-else class="empty-state"><strong>还没有任务</strong><span>创建第一个隔离任务，执行计划、诊断和安全验证。</span></div>
    </section>
  </div>
</template>
