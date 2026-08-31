<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { AUTH_REQUIRED_EVENT, getAccessToken, setAccessToken } from "@/api/client";
import ThemeToggle from "@/components/ThemeToggle.vue";

const showToken = ref(!getAccessToken());
const token = ref(getAccessToken() ?? "");
const saved = ref(false);
const authMessage = ref(showToken.value ? "请输入 .env 中 DEVPILOT_API_TOKENS 的 Token 键。" : "");
const sessionRevision = ref(0);

function requireAuthentication(event: Event): void {
  const detail = (event as CustomEvent<{ message?: string }>).detail;
  authMessage.value = detail?.message === "Invalid bearer token"
    ? "访问凭证无效，请核对 .env 中 DEVPILOT_API_TOKENS 的 Token 键。"
    : "API 需要访问凭证，请输入 .env 中 DEVPILOT_API_TOKENS 的 Token 键。";
  showToken.value = true;
}

function saveToken(): void {
  const normalized = token.value.trim();
  setAccessToken(normalized);
  if (!normalized) {
    authMessage.value = "访问凭证不能为空。";
    return;
  }
  authMessage.value = "";
  saved.value = true;
  showToken.value = false;
  sessionRevision.value += 1;
  window.setTimeout(() => (saved.value = false), 1800);
}

onMounted(() => window.addEventListener(AUTH_REQUIRED_EVENT, requireAuthentication));
onBeforeUnmount(() => window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuthentication));
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/" aria-label="DevPilot 首页">
        <span class="brand-mark" aria-hidden="true">DP</span>
        <span><strong>DevPilot</strong><small>ENGINEERING AGENT</small></span>
      </RouterLink>
      <div class="topbar-actions">
        <span class="environment"><i />本地运行时</span>
        <ThemeToggle />
        <button class="quiet-button" type="button" @click="showToken = !showToken"><span class="credential-full">访问凭证</span><span class="credential-short">凭证</span></button>
      </div>
    </header>

    <div v-if="showToken" class="token-bar">
      <div class="token-copy">
        <label for="access-token">访问凭证</label>
        <small role="status">{{ authMessage }}</small>
      </div>
      <input id="access-token" v-model="token" type="password" autocomplete="off" placeholder="Bearer Token，仅保存在当前会话" />
      <button class="button button-primary" type="button" @click="saveToken">{{ saved ? "已保存" : "保存" }}</button>
    </div>

    <main>
      <RouterView :key="sessionRevision" />
    </main>
  </div>
</template>
