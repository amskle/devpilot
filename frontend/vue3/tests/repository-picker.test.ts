import { fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { describe, expect, it, vi } from "vitest";
import { waitingState } from "./fixtures";

const mocks = vi.hoisted(() => ({ listRepositoryDirectories: vi.fn(), createTask: vi.fn(), push: vi.fn() }));
vi.mock("@/api/client", () => ({ api: mocks, ApiError: class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
} }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: mocks.push }) }));

import DashboardView from "@/views/DashboardView.vue";
import RepositoryPickerDialog from "@/components/RepositoryPickerDialog.vue";
import { ApiError } from "@/api/client";

describe("repository selection", () => {
  it("explains a missing backend route and retries after the service is updated", async () => {
    mocks.listRepositoryDirectories.mockReset();
    mocks.listRepositoryDirectories.mockRejectedValueOnce(new ApiError("Not Found", 404));
    render(RepositoryPickerDialog);
    expect((await screen.findByRole("alert")).textContent).toContain("请更新并重启 API 服务后重试");
    mocks.listRepositoryDirectories.mockResolvedValueOnce({ path: null, parent: null, items: [{ name: "repos", path: "/repos" }] });
    await fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("button", { name: "打开 repos" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("browses folders, selects the exact service path, and creates a task with it", async () => {
    mocks.listRepositoryDirectories.mockReset();
    mocks.listRepositoryDirectories.mockResolvedValueOnce({ path: null, parent: null, items: [{ name: "repos", path: "/repos" }] });
    mocks.createTask.mockResolvedValue(waitingState);
    render(DashboardView, { global: { stubs: { TaskSidebar: true } } });
    expect(screen.getByRole("button", { name: "创建并开始规划" }).hasAttribute("disabled")).toBe(true);
    await fireEvent.click(screen.getByRole("button", { name: "仓库路径 选择仓库文件夹" }));
    await screen.findByRole("button", { name: "打开 repos" });
    expect(screen.getByRole("button", { name: "选择此文件夹" }).hasAttribute("disabled")).toBe(true);
    mocks.listRepositoryDirectories.mockResolvedValueOnce({ path: "/repos", parent: null, items: [{ name: "项目 空格", path: "/repos/项目 空格" }] });
    await fireEvent.click(screen.getByRole("button", { name: "打开 repos" }));
    await screen.findByRole("button", { name: "打开 项目 空格" });
    mocks.listRepositoryDirectories.mockResolvedValueOnce({ path: "/repos/项目 空格", parent: "/repos", items: [] });
    await fireEvent.click(screen.getByRole("button", { name: "打开 项目 空格" }));
    await screen.findByText("此位置没有可浏览的子文件夹。");
    await fireEvent.click(screen.getByRole("button", { name: "选择此文件夹" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "仓库路径 /repos/项目 空格" })).toBeTruthy();
    await fireEvent.update(screen.getByRole("textbox", { name: "任务需求" }), "修复失败测试并验证");
    await fireEvent.submit(screen.getByRole("button", { name: "创建并开始规划" }).closest("form")!);
    await waitFor(() => expect(mocks.createTask).toHaveBeenCalledWith({ repo: "/repos/项目 空格", request: "修复失败测试并验证", revision: "HEAD", model: undefined }));
  });

  it("allows recovery from unavailable folders and cancellation without selecting", async () => {
    mocks.listRepositoryDirectories.mockReset();
    mocks.listRepositoryDirectories.mockRejectedValueOnce(new Error("unavailable"));
    const view = render(RepositoryPickerDialog, { props: { initialPath: "/repos/deleted" } });
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "选择此文件夹" }).hasAttribute("disabled")).toBe(true);
    mocks.listRepositoryDirectories.mockResolvedValueOnce({ path: null, parent: null, items: [] });
    await fireEvent.click(screen.getByRole("button", { name: "起始位置" }));
    await screen.findByText("此位置没有可浏览的子文件夹。");
    expect(mocks.listRepositoryDirectories).toHaveBeenLastCalledWith(undefined);
    await fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(view.emitted().close).toHaveLength(1);
    expect(view.emitted().select).toBeUndefined();
  });
});
