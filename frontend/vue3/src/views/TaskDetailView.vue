<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ApiError, api } from "@/api/client";
import ApprovalPanel from "@/components/ApprovalPanel.vue";
import BudgetPanel from "@/components/BudgetPanel.vue";
import ChangeRequestDialog from "@/components/ChangeRequestDialog.vue";
import DiffPanel from "@/components/DiffPanel.vue";
import InterventionPanel from "@/components/InterventionPanel.vue";
import PlanPanel from "@/components/PlanPanel.vue";
import RecoveryDialog from "@/components/RecoveryDialog.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import TimelinePanel from "@/components/TimelinePanel.vue";
import VerificationPanel from "@/components/VerificationPanel.vue";
import { useTaskEvents } from "@/composables/useTaskEvents";
import { compactId } from "@/domain/format";
import type { DiffDocument, Message, PlanDocument, RecoveryPoint, TaskState } from "@/domain/types";

const props = defineProps<{ taskId: string }>();
const state = ref<TaskState | null>(null);
const plan = ref<PlanDocument | PlanDocument[] | null>(null);
const diff = ref<DiffDocument | null>(null);
const messages = ref<Message[]>([]);
const recoveryPoints = ref<RecoveryPoint[]>([]);
const loading = ref(true);
const busy = ref(false);
const error = ref<string | null>(null);
const actionError = ref<string | null>(null);
const activeTab = ref<"overview" | "timeline" | "diff" | "verification">("overview");
const showRecovery = ref(false);
const showChange = ref(false);
const tabs = [
  { id: "overview", label: "总览" },
  { id: "timeline", label: "时间线" },
  { id: "diff", label: "代码变更" },
  { id: "verification", label: "验证报告" },
] as const;

const taskIdRef = computed(() => props.taskId);
const runIdRef = computed(() => state.value?.run_id ?? "");
const { events, connection } = useTaskEvents(taskIdRef, runIdRef);

const isTerminal = computed(() => state.value ? ["COMPLETED", "COMPLETED_NO_CHANGES", "CANCELLED", "FAILED", "POLICY_REJECTED"].includes(state.value.status) : true);
const modelName = computed(() => state.value?.model_profile?.model ?? "由任务价格快照固定");

async function optional<T>(request: Promise<T>, fallback: T): Promise<T> {
  try {
    return await request;
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) return fallback;
    throw caught;
  }
}

async function load(options: { quiet?: boolean } = {}): Promise<void> {
  if (!options.quiet) loading.value = true;
  error.value = null;
  try {
    const current = await api.getTask(props.taskId);
    state.value = current;
    const [nextPlan, nextDiff, nextMessages] = await Promise.all([
      optional(api.getPlan(props.taskId), null),
      optional(api.getDiff(props.taskId), null),
      optional(api.getMessages(props.taskId), []),
    ]);
    plan.value = nextPlan;
    diff.value = nextDiff;
    messages.value = nextMessages;
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "无法读取任务详情";
  } finally {
    loading.value = false;
  }
}

function handleActionFailure(caught: unknown): void {
  if (caught instanceof ApiError && caught.isConflict) {
    actionError.value = "任务状态已变化，已为你刷新。请核对最新对象后重试。";
    void load({ quiet: true });
    return;
  }
  actionError.value = caught instanceof ApiError ? caught.message : "操作未完成";
}

async function decide(decision: "APPROVE" | "REJECT"): Promise<void> {
  const current = state.value;
  const approval = current?.pending_approval;
  if (!current || !approval) return;
  busy.value = true;
  actionError.value = null;
  try {
    const payload = {
      approval_id: approval.approval_id,
      patch_hash: approval.patch_hash,
      base_revision: approval.base_revision,
      expected_state_revision: current.state_revision,
    };
    state.value = decision === "APPROVE"
      ? await api.approve(current.task_id, payload)
      : await api.reject(current.task_id, payload);
    await load({ quiet: true });
  } catch (caught) {
    handleActionFailure(caught);
  } finally {
    busy.value = false;
  }
}

async function cancelTask(): Promise<void> {
  const current = state.value;
  if (!current || !window.confirm("确认取消此任务？尚未应用或验证的 Patch 将失效。")) return;
  busy.value = true;
  actionError.value = null;
  try {
    state.value = await api.cancel(current.task_id, { expected_state_revision: current.state_revision });
  } catch (caught) {
    handleActionFailure(caught);
  } finally {
    busy.value = false;
  }
}

async function openRecovery(): Promise<void> {
  actionError.value = null;
  try {
    recoveryPoints.value = await api.getRecoveryPoints(props.taskId);
    showRecovery.value = true;
  } catch (caught) {
    handleActionFailure(caught);
  }
}

async function recover(operation: "rollback" | "restore", point: RecoveryPoint): Promise<void> {
  const current = state.value;
  if (!current) return;
  busy.value = true;
  actionError.value = null;
  const payload = { recovery_point_id: point.recovery_point_id, expected_state_revision: current.state_revision };
  try {
    state.value = operation === "rollback"
      ? await api.rollback(current.task_id, payload)
      : await api.restore(current.task_id, payload);
    showRecovery.value = false;
    await load({ quiet: true });
  } catch (caught) {
    handleActionFailure(caught);
  } finally {
    busy.value = false;
  }
}

async function submitChange(content: string, confirmed: boolean): Promise<void> {
  const current = state.value;
  if (!current) return;
  busy.value = true;
  actionError.value = null;
  try {
    state.value = await api.changeRequest(current.task_id, {
      content,
      confirm_patch_invalidation: confirmed,
      expected_state_revision: current.state_revision,
    });
    showChange.value = false;
    await load({ quiet: true });
  } catch (caught) {
    handleActionFailure(caught);
  } finally {
    busy.value = false;
  }
}

watch(
  () => events.value.at(-1),
  (event) => {
    if (event?.checkpoint_confirmed && event.state_revision !== null && state.value && event.state_revision > state.value.state_revision) {
      void load({ quiet: true });
    }
  },
);

onMounted(load);
</script>

<template>
  <div class="page detail-page">
    <div v-if="loading" class="page-loading"><i /><span>正在恢复任务控制状态…</span></div>
    <template v-else-if="state">
      <nav class="breadcrumb" aria-label="面包屑"><RouterLink to="/">任务控制台</RouterLink><span>/</span><span>{{ compactId(state.task_id) }}</span></nav>
      <header class="detail-hero">
        <div>
          <div class="title-line"><StatusBadge :status="state.status" /><span>revision {{ state.state_revision }}</span></div>
          <h1>{{ state.request || `任务 ${compactId(state.task_id)}` }}</h1>
          <p>{{ state.task_id }} · Run {{ compactId(state.run_id) }}</p>
        </div>
        <div class="detail-actions">
          <button class="button button-secondary" type="button" @click="showChange = true">提交变更需求</button>
          <button v-if="state.active_recovery_point_ref" class="button button-secondary" type="button" @click="openRecovery">恢复操作</button>
          <button v-if="!isTerminal" class="button button-danger-ghost" type="button" :disabled="busy" @click="cancelTask">取消任务</button>
        </div>
      </header>

      <p v-if="error" class="page-error" role="alert">{{ error }}</p>
      <p v-if="actionError && !state.pending_approval" class="page-error" role="alert">{{ actionError }}</p>

      <section class="run-strip" aria-label="运行信息">
        <div><span>CURRENT NODE</span><strong>{{ state.current_node.replaceAll("_", " ") }}</strong></div>
        <div><span>MODEL</span><strong>{{ modelName }}</strong></div>
        <div><span>PLAN</span><strong>{{ Array.isArray(plan) ? `v${plan.at(-1)?.version ?? "—"}` : `v${plan?.version ?? "—"}` }}</strong></div>
        <div><span>PAUSE REASON</span><strong>{{ state.pause_reason ?? "—" }}</strong></div>
      </section>

      <ApprovalPanel v-if="state.pending_approval" :approval="state.pending_approval" :busy="busy" :error="actionError" @decide="decide" />
      <InterventionPanel v-if="state.status === 'WAITING_HUMAN_INTERVENTION'" :failure="state.latest_failure" @recovery="openRecovery" @change="showChange = true" />

      <nav class="detail-tabs" aria-label="任务详情标签">
        <button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" type="button" @click="activeTab = tab.id">
          {{ tab.label }}<template v-if="tab.id === 'timeline'"> {{ events.length }}</template>
        </button>
      </nav>

      <div v-if="activeTab === 'overview'" class="detail-grid">
        <div class="main-column"><PlanPanel :plan="plan" /><VerificationPanel :verification="state.verification" /></div>
        <aside><BudgetPanel :budget="state.execution_budget" /><TimelinePanel :events="events.slice(-6)" :connection="connection" /></aside>
      </div>
      <TimelinePanel v-else-if="activeTab === 'timeline'" :events="events" :connection="connection" />
      <DiffPanel v-else-if="activeTab === 'diff'" :diff="diff" />
      <VerificationPanel v-else :verification="state.verification" />

      <section v-if="messages.length" class="panel message-panel">
        <header class="panel-header"><div><span class="eyebrow">MESSAGE HISTORY</span><h2>消息记录</h2></div><span class="safe-label">只读，不作为控制命令</span></header>
        <article v-for="message in messages" :key="message.message_id"><strong>{{ message.role }}</strong><p>{{ message.content }}</p></article>
      </section>

      <RecoveryDialog v-if="showRecovery" :points="recoveryPoints" :busy="busy" :error="actionError" @close="showRecovery = false" @execute="recover" />
      <ChangeRequestDialog v-if="showChange" :invalidates-approval="state.status === 'WAITING_RISK_APPROVAL'" :busy="busy" :error="actionError" @close="showChange = false" @submit="submitChange" />
    </template>
    <div v-else class="empty-state page-empty"><strong>任务不可用</strong><span>{{ error }}</span><RouterLink class="button button-secondary" to="/">返回控制台</RouterLink></div>
  </div>
</template>
