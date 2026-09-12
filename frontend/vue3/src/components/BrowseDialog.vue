<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

defineProps<{ title: string }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
let previousFocus: HTMLElement | null = null;

function closeOnBackdrop(event: MouseEvent): void {
  if (event.target !== dialog.value || !dialog.value) return;
  const bounds = dialog.value.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
    emit("close");
  }
}

onMounted(() => {
  previousFocus = document.activeElement as HTMLElement | null;
  dialog.value?.showModal();
});
onBeforeUnmount(() => {
  dialog.value?.close();
  previousFocus?.focus();
});
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="modal browse-dialog" :aria-label="title" @cancel.prevent="emit('close')" @click="closeOnBackdrop">
      <header><h2>{{ title }}</h2><button class="icon-button" type="button" aria-label="关闭" @click="emit('close')">×</button></header>
      <slot />
    </dialog>
  </Teleport>
</template>
