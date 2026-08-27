<script setup lang="ts">
import { computed } from "vue";
import { formatDate } from "@/domain/format";
import type { PlanDocument } from "@/domain/types";

const props = defineProps<{ plan: PlanDocument | PlanDocument[] | null }>();
const plans = computed(() => !props.plan ? [] : Array.isArray(props.plan) ? props.plan : [props.plan]);
const active = computed(() => plans.value.find((plan) => plan.status === "ACTIVE") ?? plans.value.at(-1));

function taskText(task: Record<string, unknown>): string {
  for (const key of ["title", "summary", "description", "task"]) {
    if (typeof task[key] === "string") return task[key] as string;
  }
  return JSON.stringify(task);
}
</script>

<template>
  <section class="panel plan-panel">
    <header class="panel-header">
      <div><span class="eyebrow">IMMUTABLE PLAN CHAIN</span><h2>执行计划</h2></div>
      <span v-if="active" class="version-chip">v{{ active.version }}</span>
    </header>
    <template v-if="active">
      <p class="plan-summary">{{ active.summary }}</p>
      <ol class="plan-tasks">
        <li v-for="(task, index) in active.tasks" :key="index"><i>{{ index + 1 }}</i><span>{{ taskText(task) }}</span></li>
      </ol>
      <details v-if="active.acceptance_criteria.length || active.risks.length">
        <summary>验收条件与风险</summary>
        <div class="detail-columns">
          <div><h3>验收条件</h3><ul><li v-for="item in active.acceptance_criteria" :key="item">{{ item }}</li></ul></div>
          <div><h3>风险</h3><ul><li v-for="item in active.risks" :key="item">{{ item }}</li></ul></div>
        </div>
      </details>
      <footer>创建于 {{ formatDate(active.created_at) }} · 内容哈希 {{ active.content_hash.slice(0, 12) }}</footer>
    </template>
    <div v-else class="empty-state compact"><strong>计划尚未生成</strong><span>Planning 节点完成后将在这里展示。</span></div>
  </section>
</template>
