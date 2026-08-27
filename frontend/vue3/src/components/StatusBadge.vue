<script setup lang="ts">
import { STATUS_LABELS, statusTone } from "@/domain/format";
import type { TaskStatus } from "@/domain/types";

defineProps<{ status: TaskStatus }>();
</script>

<template>
  <span class="status-badge" :class="`tone-${statusTone(status)}`">
    <i aria-hidden="true" />{{ STATUS_LABELS[status] ?? status }}
  </span>
</template>
