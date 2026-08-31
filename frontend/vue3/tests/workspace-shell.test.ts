import { fireEvent, render, screen } from "@testing-library/vue";
import { describe, expect, it } from "vitest";
import App from "@/App.vue";
import MessageComposer from "@/components/MessageComposer.vue";
import ThemeToggle from "@/components/ThemeToggle.vue";
import { AUTH_REQUIRED_EVENT } from "@/api/client";

describe("conversation workspace controls", () => {
  it("prompts for a token and saves it for the current browser session", async () => {
    render(App, {
      global: {
        stubs: {
          RouterLink: { template: "<a><slot /></a>" },
          RouterView: { template: "<div />" },
        },
      },
    });

    const input = screen.getByLabelText("访问凭证");
    expect(screen.getByText(/DEVPILOT_API_TOKENS/)).toBeTruthy();
    await fireEvent.update(input, "test-access-token");
    await fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(window.sessionStorage.getItem("devpilot.access-token")).toBe("test-access-token");
    expect(screen.queryByLabelText("访问凭证")).toBeNull();
  });

  it("reopens the credential prompt when an API request returns 401", async () => {
    window.sessionStorage.setItem("devpilot.access-token", "expired-token");
    render(App, {
      global: {
        stubs: {
          RouterLink: { template: "<a><slot /></a>" },
          RouterView: { template: "<div />" },
        },
      },
    });

    expect(screen.queryByLabelText("访问凭证")).toBeNull();
    window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT, {
      detail: { message: "Invalid bearer token" },
    }));

    expect(await screen.findByLabelText("访问凭证")).toBeTruthy();
    expect(screen.getByText(/访问凭证无效/)).toBeTruthy();
  });

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
