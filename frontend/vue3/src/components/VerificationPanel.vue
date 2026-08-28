<script setup lang="ts">
import type { VerificationResult } from "@/domain/types";
defineProps<{ verification: VerificationResult | null }>();
</script>

<template>
  <section class="panel verification-panel">
    <header class="panel-header">
      <h2>验证报告</h2>
      <span v-if="verification?.passed !== undefined" class="result-chip" :class="verification.passed ? 'pass' : 'fail'">
        {{ verification.passed ? "PASS" : "FAIL" }}
      </span>
    </header>
    <template v-if="verification">
      <p>{{ verification.summary ?? "验证命令已完成。" }}</p>
      <dl class="report-grid">
        <div><dt>退出码</dt><dd>{{ verification.exit_code ?? "—" }}</dd></div>
        <div><dt>失败用例</dt><dd>{{ verification.failed_test_ids?.length ?? 0 }}</dd></div>
        <div class="wide"><dt>命令</dt><dd><code>{{ verification.command ?? "—" }}</code></dd></div>
      </dl>
      <ul v-if="verification.failed_test_ids?.length" class="failure-list">
        <li v-for="test in verification.failed_test_ids" :key="test">{{ test }}</li>
      </ul>
    </template>
    <div v-else class="empty-state compact"><strong>尚未执行验证</strong><span>结果仅由退出码和结构化报告决定。</span></div>
  </section>
</template>
