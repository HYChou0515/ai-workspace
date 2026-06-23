import { describe, expect, it } from "vitest";

import { chatEmptyHint } from "./chatCopy";

describe("chatEmptyHint", () => {
  it("invites a question in plain language", () => {
    expect(chatEmptyHint(false).toLowerCase()).toContain("ask the agent");
  });

  it("never leaks RCA-specific system jargon (it's a shared panel)", () => {
    const hint = chatEmptyHint(true).toLowerCase();
    for (const word of ["notebook", "brief", "analyses", "sandbox", "evidence", "report"]) {
      expect(hint).not.toContain(word);
    }
  });

  it("points to the example chips only when there are some", () => {
    expect(chatEmptyHint(true).toLowerCase()).toContain("example");
    expect(chatEmptyHint(false).toLowerCase()).not.toContain("example");
  });
});
