import { render, screen } from "@testing-library/vue";
import { describe, expect, it } from "vitest";
import TimelinePanel from "@/components/TimelinePanel.vue";
import { event } from "./fixtures";

describe("optimized Phase 2/3 presentation", () => {
  it("distinguishes revision-bound, pending, and independent events", () => {
    render(TimelinePanel, {
      props: {
        connection: "connected",
        events: [
          event(1, { state_revision: 7, checkpoint_confirmed: true }),
          event(2, { state_revision: 8, checkpoint_confirmed: false }),
          event(3, { state_revision: null, checkpoint_confirmed: false, event_type: "user_message" }),
        ],
      },
    });

    expect(screen.getByText("revision 7")).toBeTruthy();
    expect(screen.getByText("等待 checkpoint")).toBeTruthy();
    expect(screen.getByText("独立事件")).toBeTruthy();
  });
});
