import { createRouter, createWebHistory } from "vue-router";

import DashboardView from "@/views/DashboardView.vue";
import TaskDetailView from "@/views/TaskDetailView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "dashboard", component: DashboardView },
    { path: "/tasks/:taskId", name: "task-detail", component: TaskDetailView, props: true },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
