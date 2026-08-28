<script setup lang="ts">
import { computed, ref } from "vue";

const props = defineProps<{ invalidatesApproval: boolean; busy?: boolean; error?: string | null }>();
const emit = defineEmits<{ close: []; submit: [content: string, confirmed: boolean] }>();
const content = ref("");
const confirmed = ref(false);
const valid = computed(() => content.value.trim().length >= 5 && (!props.invalidatesApproval || confirmed.value));
</script>

<template>
  <div class="modal-backdrop" role="presentation" @click.self="emit('close')">
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="change-title">
      <header><h2 id="change-title">提交变更需求</h2><button class="icon-button" type="button" aria-label="关闭" @click="emit('close')">×</button></header>
      <p>这是正式控制操作，会进入 ChangeRequest → ReplanRequest 审计链。普通消息不会修改 Plan。</p>
      <label class="field"><span>新的约束或目标</span><textarea v-model="content" rows="7" placeholder="清晰描述需要调整的目标、验收条件或限制…" /></label>
      <div v-if="invalidatesApproval" class="warning-callout"><strong>当前 Patch 正在等待审批</strong><span>接受本次变更将废弃现有 Approval 和 Patch Proposal，并重新规划。</span></div>
      <label v-if="invalidatesApproval" class="confirm-check"><input v-model="confirmed" type="checkbox" />确认废弃当前待审批 Patch</label>
      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <footer class="modal-actions">
        <button class="button button-secondary" type="button" @click="emit('close')">取消</button>
        <button class="button button-primary" type="button" :disabled="!valid || busy" @click="emit('submit', content.trim(), confirmed)">{{ busy ? "正在提交…" : "提交并重新规划" }}</button>
      </footer>
    </section>
  </div>
</template>
