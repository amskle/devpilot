<script setup lang="ts">
import { ref } from "vue";
import { getAccessToken, setAccessToken } from "@/api/client";

const showToken = ref(false);
const token = ref(getAccessToken() ?? "");
const saved = ref(false);

function saveToken(): void {
  setAccessToken(token.value);
  saved.value = true;
  window.setTimeout(() => (saved.value = false), 1800);
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/" aria-label="DevPilot 首页">
        <span class="brand-mark" aria-hidden="true">DP</span>
        <span><strong>DevPilot</strong><small>CONTROL ROOM</small></span>
      </RouterLink>
      <div class="topbar-actions">
        <span class="environment"><i /> LOCAL RUNTIME</span>
        <button class="quiet-button" type="button" @click="showToken = !showToken">访问凭证</button>
      </div>
    </header>

    <div v-if="showToken" class="token-bar">
      <label for="access-token">Bearer Token</label>
      <input id="access-token" v-model="token" type="password" autocomplete="off" placeholder="仅保存在当前浏览器会话" />
      <button class="button button-primary" type="button" @click="saveToken">{{ saved ? "已保存" : "保存" }}</button>
    </div>

    <main>
      <RouterView />
    </main>
  </div>
</template>
