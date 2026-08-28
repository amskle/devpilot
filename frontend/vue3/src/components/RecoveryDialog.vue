<script setup lang="ts">
import { ref } from "vue";
import { compactId, formatDate } from "@/domain/format";
import type { RecoveryPoint } from "@/domain/types";

defineProps<{ points: RecoveryPoint[]; busy?: boolean; error?: string | null }>();
const emit = defineEmits<{
  close: [];
  execute: [operation: "rollback" | "restore", point: RecoveryPoint];
}>();
const selected = ref<string | null>(null);
const confirmed = ref(false);
</script>

<template>
  <div class="modal-backdrop" role="presentation" @click.self="emit('close')">
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="recovery-title">
      <header><h2 id="recovery-title">选择恢复点</h2><button class="icon-button" type="button" aria-label="关闭" @click="emit('close')">×</button></header>
      <p>回滚是当前 Run 内的补偿操作；完整恢复会创建新 Run，并保留原 Run 审计历史。</p>
      <div class="recovery-list">
        <label v-for="point in points" :key="point.recovery_point_id" :class="{ selected: selected === point.recovery_point_id }">
          <input v-model="selected" type="radio" name="recovery" :value="point.recovery_point_id" />
          <span><strong>Plan v{{ point.plan_version }}</strong><small>{{ formatDate(point.created_at) }}</small></span>
          <code>{{ compactId(point.repository_snapshot_id) }}</code>
        </label>
      </div>
      <div v-if="!points.length" class="empty-state compact"><strong>没有可用恢复点</strong></div>
      <label class="confirm-check"><input v-model="confirmed" type="checkbox" />我理解恢复会使下游 Patch 和验证结果失效</label>
      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <footer class="modal-actions">
        <button class="button button-secondary" type="button" @click="emit('close')">取消</button>
        <button class="button button-danger-ghost" type="button" :disabled="!selected || !confirmed || busy" @click="emit('execute', 'rollback', points.find((item) => item.recovery_point_id === selected)!)">回滚代码</button>
        <button class="button button-primary" type="button" :disabled="!selected || !confirmed || busy" @click="emit('execute', 'restore', points.find((item) => item.recovery_point_id === selected)!)">完整恢复</button>
      </footer>
    </section>
  </div>
</template>
