<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "@/api/client";
import TaskSidebar from "@/components/TaskSidebar.vue";
import type { TaskState } from "@/domain/types";

const router = useRouter();
const creating = ref(false);
const error = ref<string | null>(null);
const form = ref({ repo: "", request: "", revision: "HEAD", model: "" });

async function create(): Promise<void> {
  creating.value = true;
  error.value = null;
  try {
    const result: TaskState = await api.createTask({ ...form.value, model: form.value.model || undefined });
    await router.push(`/tasks/${result.task_id}`);
  } catch (caught) {
    error.value = caught instanceof ApiError
      ? `${caught.message} 请核对仓库路径和服务连接后重试。`
      : "任务未创建。请核对仓库路径和服务连接后重试。";
  } finally {
    creating.value = false;
  }
}
</script>

<template>
  <div class="workspace-shell">
    <TaskSidebar />

    <section class="conversation-workspace" aria-labelledby="new-task-title">
      <header class="conversation-header">
        <div>
          <h1 id="new-task-title">新任务</h1>
          <p>描述目标，DevPilot 会在隔离工作区内规划、修改并验证。</p>
        </div>
        <span class="context-chip"><i aria-hidden="true" />安全边界已启用</span>
      </header>

      <div class="conversation-scroll welcome-thread">
        <article class="message message-assistant">
          <div class="message-avatar" aria-hidden="true">DP</div>
          <div class="message-body">
            <header><strong>DevPilot</strong><span>任务准备</span></header>
            <h2>想让这个仓库发生什么变化？</h2>
            <p>提供代码仓库和清晰目标。我会先生成可审计的计划，再执行诊断、代码修改与确定性验证。</p>
            <ul class="capability-list">
              <li><span>计划</span>计划版本会保留完整修订链。</li>
              <li><span>审批</span>高风险 Patch 必须由你明确批准。</li>
              <li><span>恢复</span>失败路径保留恢复点与事件证据。</li>
            </ul>
          </div>
        </article>
        <p v-if="error" class="page-error" role="alert">{{ error }}</p>
      </div>

      <form class="create-composer" @submit.prevent="create">
        <div class="create-context">
          <label class="field repo-field">
            <span>仓库路径</span>
            <input v-model="form.repo" required placeholder="C:\projects\example" />
          </label>
          <details class="composer-options">
            <summary>运行选项</summary>
            <div>
              <label class="field"><span>Git revision</span><input v-model="form.revision" required /></label>
              <label class="field"><span>模型（可选）</span><input v-model="form.model" placeholder="使用服务默认模型" /></label>
            </div>
          </details>
        </div>
        <label class="field request-field">
          <span>任务需求</span>
          <textarea v-model="form.request" required minlength="5" rows="3" placeholder="例如：修复登录超时后重复提交的问题，并补充回归测试" />
        </label>
        <div class="create-actions">
          <span>任务会在独立 worktree 中运行</span>
          <button class="button button-primary" type="submit" :disabled="creating">
            {{ creating ? "正在创建…" : "创建并开始规划" }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>
