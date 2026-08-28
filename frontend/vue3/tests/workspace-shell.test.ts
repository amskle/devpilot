import { fireEvent, render, screen } from "@testing-library/vue";
import { describe, expect, it } from "vitest";
import MessageComposer from "@/components/MessageComposer.vue";
import ThemeToggle from "@/components/ThemeToggle.vue";

describe("conversation workspace controls", () => {
  it("switches between day and night themes and persists the choice", async () => {
    render(ThemeToggle);

    await fireEvent.click(screen.getByRole("button", { name: "切换到夜间模式" }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    const persisted = typeof window.localStorage?.getItem === "function"
      ? window.localStorage.getItem("devpilot.theme")
      : window.sessionStorage.getItem("devpilot.theme");
    expect(persisted).toBe("dark");
    expect(screen.getByRole("button", { name: "切换到白天模式" })).toBeTruthy();
  });

  it("keeps ordinary messages separate from formal change requests", async () => {
    const view = render(MessageComposer, { props: { modelValue: "请说明当前进展" } });

    await fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    await fireEvent.click(screen.getByRole("button", { name: "作为变更需求" }));

    expect(view.emitted().send).toHaveLength(1);
    expect(view.emitted().change).toHaveLength(1);
  });
});
