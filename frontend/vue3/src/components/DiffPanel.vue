<script setup lang="ts">
import { computed, ref } from "vue";
import type { DiffDocument } from "@/domain/types";

const props = defineProps<{ diff: DiffDocument | null }>();
const activeFile = ref<string | null>(null);

const lines = computed(() => (props.diff?.text ?? "").split("\n"));
const visibleLines = computed(() => {
  if (!activeFile.value) return lines.value;
  const result: string[] = [];
  let include = false;
  for (const line of lines.value) {
    if (line.startsWith("diff --git ")) include = line.includes(` b/${activeFile.value}`);
    if (include) result.push(line);
  }
  return result;
});

function kind(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("+")) return "added";
  if (line.startsWith("-")) return "removed";
  if (line.startsWith("@@")) return "hunk";
  return "context";
}
</script>

<template>
  <section class="panel diff-panel">
    <header class="panel-header">
      <h2>代码变更</h2>
      <span v-if="diff" class="hash-chip">{{ diff.patch_hash?.slice(0, 10) }}</span>
    </header>
    <template v-if="diff?.text">
      <nav class="file-tabs" aria-label="变更文件">
        <button :class="{ active: !activeFile }" type="button" @click="activeFile = null">全部</button>
        <button v-for="file in diff.changed_files" :key="file" :class="{ active: activeFile === file }" type="button" @click="activeFile = file">{{ file }}</button>
      </nav>
      <pre class="diff-code" aria-label="Unified diff"><code><span v-for="(line, index) in visibleLines" :key="index" :class="`diff-${kind(line)}`"><i>{{ index + 1 }}</i>{{ line || " " }}
</span></code></pre>
    </template>
    <div v-else class="empty-state compact"><strong>暂无变更</strong><span>Patch 提案生成后可在此安全查看文本 Diff。</span></div>
  </section>
</template>
