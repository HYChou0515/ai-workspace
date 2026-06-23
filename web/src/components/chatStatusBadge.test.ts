import { describe, expect, it } from "vitest";

import { chatStatusBadge } from "./chatStatusBadge";

describe("chatStatusBadge", () => {
  it("maps an active run to a running badge", () => {
    expect(chatStatusBadge("running")?.label).toBe("running");
    expect(chatStatusBadge("pending")?.label).toBe("running");
  });

  it("maps an awaiting-human run to an awaiting badge", () => {
    expect(chatStatusBadge("awaiting_human")?.label).toBe("awaiting");
  });

  it("maps terminal runs to their own badges", () => {
    expect(chatStatusBadge("done")?.label).toBe("done");
    expect(chatStatusBadge("error")?.label).toBe("error");
    expect(chatStatusBadge("cancelled")?.label).toBe("cancelled");
  });

  it("has no badge for a free chat (null status) or an unknown value", () => {
    expect(chatStatusBadge(null)).toBeNull();
    expect(chatStatusBadge("weird")).toBeNull();
  });
});
