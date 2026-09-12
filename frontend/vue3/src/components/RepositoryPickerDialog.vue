<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, ApiError, type RepositoryDirectories } from "@/api/client";
import BrowseDialog from "@/components/BrowseDialog.vue";

const props = defineProps<{ initialPath?: string }>();
const emit = defineEmits<{ close: []; select: [path: string] }>();
const listing = ref<RepositoryDirectories>({ path: null, parent: null, items: [] });
const loading = ref(false);
const error = ref<string | null>(null);
const query = ref("");
const requestedPath = ref<string | undefined>();
const directories = computed(() => listing.value.items.filter((entry) => entry.name.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())));

async function load(path?: string): Promise<void> {
  requestedPath.value = path;
  loading.value = true;
  error.value = null;
  query.value = "";
  try {
    listing.value = await api.listRepositoryDirectories(path);
  } catch (caught) {
    error.value = caught instanceof ApiError && caught.status === 404
      ? "当前后端未提供文件夹浏览接口，请更新并重启 API 服务后重试。"
      : caught instanceof ApiError ? caught.message : "无法读取文件夹，请重试。";
  } finally {
    loading.value = false;
  }
}

onMounted(() => load(props.initialPath || undefined));
</script>

<template>
  <BrowseDialog title="选择仓库文件夹" @close="emit('close')">
    <p>浏览服务可访问的文件夹，打开仓库目录后点击“选择此文件夹”。</p>
    <nav class="directory-toolbar" aria-label="文件夹导航">
      <button class="button button-secondary" type="button" :disabled="loading" @click="load()">起始位置</button>
      <button class="button button-secondary" type="button" :disabled="loading || !listing.path" @click="load(listing.parent || undefined)">上一级</button>
    </nav>
    <div class="directory-path">{{ listing.path || '可用位置' }}</div>
    <label class="field"><span>筛选文件夹</span><input v-model="query" autofocus type="search" placeholder="按文件夹名称筛选" /></label>
    <div class="browse-results" aria-live="polite" :aria-busy="loading">
      <p v-if="loading" class="sidebar-empty">正在读取文件夹…</p>
      <div v-else-if="error" class="sidebar-error" role="alert"><span>{{ error }}</span><button type="button" @click="load(requestedPath)">重试</button></div>
      <template v-else>
        <button v-for="entry in directories" :key="entry.path" class="directory-item" type="button" :aria-label="`打开 ${entry.name}`" @click="load(entry.path)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5h6l2 2h10v13H3Z" /></svg>
          <span>{{ entry.name }}</span><span aria-hidden="true">›</span>
        </button>
        <p v-if="!directories.length" class="sidebar-empty">{{ query ? '没有匹配的文件夹。' : '此位置没有可浏览的子文件夹。' }}</p>
      </template>
    </div>
    <footer class="modal-actions">
      <button class="button button-secondary" type="button" @click="emit('close')">取消</button>
      <button class="button button-primary" type="button" :disabled="loading || !!error || !listing.path" @click="listing.path && emit('select', listing.path)">选择此文件夹</button>
    </footer>
  </BrowseDialog>
</template>
