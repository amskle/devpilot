<script setup lang="ts">
import { computed, ref } from "vue";
import { compactId, formatDate } from "@/domain/format";
import type { ApprovalRequest } from "@/domain/types";

const props = defineProps<{
  approval: ApprovalRequest;
  busy?: boolean;
  error?: string | null;
}>();
const emit = defineEmits<{ decide: [decision: "APPROVE" | "REJECT"] }>();
const confirmed = ref(false);
const expired = computed(() => Date.now() >= new Date(props.approval.expires_at).valueOf());
</script>

<template>
  <section class="panel action-panel approval-panel">
    <div class="action-icon warning" aria-hidden="true">!</div>
    <div class="action-body">
      <h2>高风险变更等待审批</h2>
      <p>请核对 Patch、基础版本和风险报告。批准仅对当前对象生效，状态变化后会被拒绝。</p>
      <dl class="target-grid">
        <div><dt>审批 ID</dt><dd :title="approval.approval_id">{{ compactId(approval.approval_id) }}</dd></div>
        <div><dt>Patch hash</dt><dd>{{ approval.patch_hash.slice(0, 14) }}</dd></div>
        <div><dt>基础版本</dt><dd>{{ compactId(approval.base_revision) }}</dd></div>
        <div><dt>过期时间</dt><dd>{{ formatDate(approval.expires_at) }}</dd></div>
      </dl>
      <label class="confirm-check"><input v-model="confirmed" type="checkbox" />我已核对当前 Patch 与风险信息</label>
      <p v-if="expired" class="inline-error">审批已过期，请刷新任务状态。</p>
      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <div class="action-row">
        <button class="button button-danger-ghost" type="button" :disabled="busy || expired" @click="emit('decide', 'REJECT')">拒绝变更</button>
        <button class="button button-warning" type="button" :disabled="busy || expired || !confirmed" @click="emit('decide', 'APPROVE')">
          {{ busy ? "正在提交…" : "批准并继续" }}
        </button>
      </div>
    </div>
  </section>
</template>
