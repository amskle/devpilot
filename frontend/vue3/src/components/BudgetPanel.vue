<script setup lang="ts">
import { computed } from "vue";
import { percent } from "@/domain/format";
import type { ExecutionBudget } from "@/domain/types";

const props = defineProps<{ budget: ExecutionBudget }>();

const rows = computed(() => [
  { label: "迭代", used: props.budget.iterations_used, max: props.budget.max_iterations },
  { label: "计划修订", used: props.budget.plan_revisions_used, max: props.budget.max_plan_revisions },
  { label: "模型调用", used: props.budget.llm_calls_used, max: props.budget.max_llm_calls },
  { label: "工具调用", used: props.budget.tool_calls_used, max: props.budget.max_tool_calls },
  {
    label: "Token",
    used: props.budget.prompt_tokens_used + props.budget.completion_tokens_used,
    max: props.budget.max_total_tokens,
  },
]);
</script>

<template>
  <section class="panel budget-panel">
    <header class="panel-header"><h2>执行预算</h2></header>
    <div class="budget-list">
      <div v-for="row in rows" :key="row.label" class="budget-row">
        <div><span>{{ row.label }}</span><strong>{{ row.used }} / {{ row.max }}</strong></div>
        <div class="meter"><i :style="{ width: `${percent(row.used, row.max)}%` }" /></div>
      </div>
    </div>
    <div class="cost-line">
      <span>累计费用</span>
      <strong>{{ budget.cost_currency }} {{ budget.cost_used }}</strong>
      <small v-if="budget.max_cost">上限 {{ budget.max_cost }}</small>
    </div>
  </section>
</template>
