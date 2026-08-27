import { render, screen } from "@testing-library/vue";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import { waitingState } from "./fixtures";

const apiMock = vi.hoisted(() => ({
  getTask: vi.fn(),
  getPlan: vi.fn(),
  getDiff: vi.fn(),
  getMessages: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {
    status = 500;
    isConflict = false;
  },
}));

vi.mock("@/composables/useTaskEvents", () => ({
  useTaskEvents: () => ({ events: ref([]), connection: ref("connected") }),
}));

import TaskDetailView from "@/views/TaskDetailView.vue";

describe("TaskDetailView", () => {
  it("shows the task-selected model returned from the pricing snapshot", async () => {
    apiMock.getTask.mockResolvedValue(waitingState);
    apiMock.getPlan.mockResolvedValue(null);
    apiMock.getDiff.mockResolvedValue(null);
    apiMock.getMessages.mockResolvedValue([]);

    render(TaskDetailView, {
      props: { taskId: "task_1" },
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });

    expect(await screen.findByText("qwen3.7-flash")).toBeTruthy();
  });
});
