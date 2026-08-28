<script setup lang="ts">
import { computed } from "vue";
import { useTheme } from "@/composables/useTheme";

const { theme, toggleTheme } = useTheme();
const label = computed(() => theme.value === "light" ? "切换到夜间模式" : "切换到白天模式");
</script>

<template>
  <button class="theme-toggle" type="button" :aria-label="label" :aria-pressed="theme === 'dark'" :title="label" @click="toggleTheme">
    <svg v-if="theme === 'light'" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="3.5" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
    <svg v-else viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20.4 15.1A8.4 8.4 0 0 1 8.9 3.6 8.5 8.5 0 1 0 20.4 15.1Z" />
    </svg>
    <span>{{ theme === "light" ? "白天" : "夜间" }}</span>
  </button>
</template>
