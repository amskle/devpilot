<script setup lang="ts">
import type { FailureRecord } from "@/domain/types";

defineProps<{ failure: FailureRecord | null }>();
defineEmits<{ recovery: []; change: [] }>();
</script>

<template>
  <section class="panel action-panel intervention-panel">
    <div class="action-icon danger" aria-hidden="true">×</div>
    <div class="action-body">
      <span class="eyebrow">MANUAL INTERVENTION</span>
      <h2>自动执行已安全停止</h2>
      <p>{{ failure?.summary ?? "任务需要人工判断后才能继续。" }}</p>
      <div v-if="failure" class="failure-code">
        <code>{{ failure.category }} / {{ failure.error_code }}</code>
        <span>建议动作：{{ failure.recovery_action }}</span>
      </div>
      <div class="action-row">
        <button class="button button-secondary" type="button" @click="$emit('change')">提交修正要求</button>
        <button class="button button-primary" type="button" @click="$emit('recovery')">查看恢复点</button>
      </div>
    </div>
  </section>
</template>
