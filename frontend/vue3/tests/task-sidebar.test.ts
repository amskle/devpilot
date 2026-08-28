import { fireEvent, render, screen } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";
import { budget } from "./fixtures";

const apiMock = vi.hoisted(() => ({ listTasks: vi.fn() }));

vi.mock("@/api/client", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}));

import TaskSidebar from "@/components/TaskSidebar.vue";

describe("TaskSidebar", () => {
  it("keeps task history visible and metrics behind a disclosure", async () => {
    apiMock.listTasks.mockResolvedValue({
      next_cursor: null,
      items: [
        { task_id: "task_1", run_id: "run_1", status: "RUNNING", current_node: "planning", state_revision: 1, pause_reason: null, request: "修复缓存失效", execution_budget: budget },
        { task_id: "task_2", run_id: "run_2", status: "COMPLETED", current_node: "end", state_revision: 4, pause_reason: null, request: "补充回归测试", execution_budget: budget },
      ],
    });

    const view = render(TaskSidebar, {
      props: { currentTaskId: "task_1" },
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });

    expect(await screen.findByText("修复缓存失效")).toBeTruthy();
    const disclosure = view.container.querySelector("details");
    expect(disclosure?.open).toBe(false);

    await fireEvent.click(screen.getByText("运行概览"));

    expect(disclosure?.open).toBe(true);
    expect(screen.getByText("任务总数")).toBeTruthy();
    expect(screen.getByText("需要处理")).toBeTruthy();
  });
});
