import { fireEvent, render, screen } from "@testing-library/vue";
import { describe, expect, it } from "vitest";
import ApprovalPanel from "@/components/ApprovalPanel.vue";
import ChangeRequestDialog from "@/components/ChangeRequestDialog.vue";
import { approval } from "./fixtures";

describe("human-in-the-loop controls", () => {
  it("requires explicit confirmation before approval", async () => {
    const view = render(ApprovalPanel, { props: { approval } });
    const approve = screen.getByRole("button", { name: "批准并继续" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);

    await fireEvent.click(screen.getByRole("checkbox"));
    expect((approve as HTMLButtonElement).disabled).toBe(false);
    await fireEvent.click(approve);

    expect(view.emitted().decide?.[0]).toEqual(["APPROVE"]);
  });

  it("cannot invalidate a pending patch without second confirmation", async () => {
    const view = render(ChangeRequestDialog, { props: { invalidatesApproval: true } });
    const submit = screen.getByRole("button", { name: "提交并重新规划" });
    await fireEvent.update(screen.getByRole("textbox"), "Update the acceptance criteria");
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    await fireEvent.click(screen.getByRole("checkbox"));
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    await fireEvent.click(submit);

    expect(view.emitted().submit?.[0]).toEqual(["Update the acceptance criteria", true]);
  });
});
