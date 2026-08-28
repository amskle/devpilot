<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ modelValue: string; busy?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: string]; send: []; change: [] }>();
const canSend = computed(() => props.modelValue.trim().length > 0 && !props.busy);

function send(): void {
  if (canSend.value) emit("send");
}
</script>

<template>
  <form class="composer" @submit.prevent="send">
    <div class="composer-label"><label for="task-message">给任务发消息</label><span>普通消息不会修改 Plan</span></div>
    <textarea
      id="task-message"
      :value="modelValue"
      rows="2"
      placeholder="补充上下文、询问进展或记录说明"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      @keydown.ctrl.enter.prevent="send"
    />
    <div class="composer-actions">
      <button class="button button-secondary" type="button" @click="emit('change')">作为变更需求</button>
      <span>Ctrl + Enter 发送</span>
      <button class="button button-primary" type="submit" :disabled="!canSend">{{ busy ? "正在发送…" : "发送消息" }}</button>
    </div>
  </form>
</template>
