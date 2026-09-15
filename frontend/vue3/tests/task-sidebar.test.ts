import { fireEvent, render, screen, within } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";
import { budget } from "./fixtures";

const apiMock = vi.hoisted(() => ({
  listTasks: vi.fn(),
  deleteTask: vi.fn(),
  deleteTasks: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}));

import TaskSidebar from "@/components/TaskSidebar.vue";

describe("TaskSidebar", () => {
  it("selects and deletes multiple terminal tasks in one request", async () => {
    const tasks = [
      { task_id: "task_1", status: "COMPLETED", request: "旧任务一", execution_budget: budget },
      { task_id: "task_2", status: "FAILED", request: "旧任务二", execution_budget: budget },
      { task_id: "task_3", status: "RUNNING", request: "运行中任务", execution_budget: budget },
    ];
    apiMock.listTasks.mockReset();
    apiMock.deleteTasks.mockReset();
    apiMock.listTasks.mockResolvedValue({ items: tasks, next_cursor: null });
    apiMock.deleteTasks.mockResolvedValue({ deleted_task_ids: ["task_1", "task_2"] });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(TaskSidebar, { global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } } });

    await screen.findByText("旧任务一");
    await fireEvent.click(screen.getByRole("button", { name: "查看更多" }));
    await screen.findByRole("dialog", { name: "查询任务历史" });
    await fireEvent.click(screen.getByRole("checkbox", { name: "全选本页可删除任务" }));
    await fireEvent.click(screen.getByRole("button", { name: "删除所选（2）" }));

    expect(apiMock.deleteTasks).toHaveBeenCalledWith(["task_1", "task_2"]);
    expect(screen.queryByText("旧任务一")).toBeNull();
    expect(screen.queryByText("旧任务二")).toBeNull();
    expect(screen.getByText("运行中任务")).toBeTruthy();
  });

  it("permanently deletes a terminal task after confirmation", async () => {
    apiMock.listTasks.mockReset();
    apiMock.deleteTask.mockReset();
    apiMock.listTasks.mockResolvedValue({
      items: [{ task_id: "task_old", status: "COMPLETED", request: "旧任务", execution_budget: budget }],
      next_cursor: null,
    });
    apiMock.deleteTask.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(TaskSidebar, { global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } } });

    await screen.findByText("旧任务");
    await fireEvent.click(screen.getByRole("button", { name: "查看更多" }));
    await screen.findByRole("dialog", { name: "查询任务历史" });
    await fireEvent.click(screen.getByRole("button", { name: "删除任务 旧任务" }));

    expect(apiMock.deleteTask).toHaveBeenCalledWith("task_old");
    expect(screen.queryByRole("button", { name: "删除任务 旧任务" })).toBeNull();
  });

  it("shows ten recent tasks and searches older tasks with pagination in a dialog", async () => {
    const tasks = Array.from({ length: 10 }, (_, index) => ({
      task_id: `task_${index}`, status: "COMPLETED", request: `最近任务 ${index}`, execution_budget: budget,
    }));
    apiMock.listTasks.mockReset();
    apiMock.listTasks.mockResolvedValueOnce({ items: tasks, next_cursor: "page2" });
    const view = render(TaskSidebar, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await screen.findByText("最近任务 9");
    expect(view.container.querySelectorAll(".history-item")).toHaveLength(10);
    expect(apiMock.listTasks).toHaveBeenCalledWith({ limit: 10 });
    apiMock.listTasks.mockResolvedValueOnce({ items: tasks, next_cursor: "page2" });
    await fireEvent.click(screen.getByRole("button", { name: "查看更多" }));
    const dialog = within(await screen.findByRole("dialog", { name: "查询任务历史" }));
    await dialog.findByText("最近任务 9");
    apiMock.listTasks.mockResolvedValueOnce({ items: [{ ...tasks[0], task_id: "older", request: "历史任务" }], next_cursor: null });
    await fireEvent.click(dialog.getByRole("button", { name: "下一页" }));
    await dialog.findByText("历史任务");
    expect(apiMock.listTasks).toHaveBeenLastCalledWith({ limit: 10, query: "", cursor: "page2" });
    apiMock.listTasks.mockResolvedValueOnce({ items: [{ ...tasks[0], task_id: "very_old", request: "百条以前的任务" }], next_cursor: null });
    await fireEvent.update(dialog.getByRole("searchbox"), "百条以前");
    await fireEvent.submit(dialog.getByRole("button", { name: "查询" }).closest("form")!);
    await dialog.findByText("百条以前的任务");
    expect(apiMock.listTasks).toHaveBeenLastCalledWith({ limit: 10, query: "百条以前", cursor: undefined });
    expect(dialog.getByText("第 1 页")).toBeTruthy();
    expect(dialog.getByRole("button", { name: "下一页" }).hasAttribute("disabled")).toBe(true);
    await fireEvent.click(dialog.getByText("百条以前的任务"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(view.container.querySelectorAll(".history-item")).toHaveLength(10);
  });

  it("shows query failures with retry and supports empty results and Escape", async () => {
    apiMock.listTasks.mockReset();
    apiMock.listTasks.mockResolvedValueOnce({ items: [], next_cursor: null });
    render(TaskSidebar, { global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } } });
    await screen.findByText("暂无任务。");
    apiMock.listTasks.mockRejectedValueOnce(new Error("offline"));
    await fireEvent.click(screen.getByRole("button", { name: "查看更多" }));
    await screen.findByRole("alert");
    apiMock.listTasks.mockResolvedValueOnce({ items: [], next_cursor: null });
    await fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("没有匹配的任务。");
    await fireEvent(screen.getByRole("dialog"), new Event("cancel"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

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
    expect(screen.getByText("Token 消耗")).toBeTruthy();
    expect(screen.getByText("3,000")).toBeTruthy();
  });
});
