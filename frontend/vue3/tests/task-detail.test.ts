import { fireEvent, render, screen } from "@testing-library/vue";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import { waitingState } from "./fixtures";

const apiMock = vi.hoisted(() => ({
  getTask: vi.fn(),
  getPlan: vi.fn(),
  getDiff: vi.fn(),
  getMessages: vi.fn(),
  listTasks: vi.fn(),
  sendMessage: vi.fn(),
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
    apiMock.listTasks.mockResolvedValue({ items: [], next_cursor: null });

    render(TaskDetailView, {
      props: { taskId: "task_1" },
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });

    expect(await screen.findByText("qwen3.7-flash")).toBeTruthy();
  });

  it("sends ordinary conversation text through the message endpoint", async () => {
    apiMock.getTask.mockResolvedValue(waitingState);
    apiMock.getPlan.mockResolvedValue(null);
    apiMock.getDiff.mockResolvedValue(null);
    apiMock.getMessages.mockResolvedValue([]);
    apiMock.listTasks.mockResolvedValue({ items: [], next_cursor: null });
    apiMock.sendMessage.mockResolvedValue({
      message_id: "message_1",
      role: "user",
      content: "补充：请保留旧接口兼容性",
      created_at: "2026-08-27T00:00:00Z",
    });

    render(TaskDetailView, {
      props: { taskId: "task_1" },
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });

    const composer = await screen.findByLabelText("给任务发消息");
    await fireEvent.update(composer, "补充：请保留旧接口兼容性");
    await fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    expect(apiMock.sendMessage).toHaveBeenCalledWith("task_1", { content: "补充：请保留旧接口兼容性" });
    expect(await screen.findByText("补充：请保留旧接口兼容性")).toBeTruthy();
  });
});
