/** plan-workflow-output-repair §3.5: a workflow repair request — what the engine
 * asked a node to re-emit — reaches the screen as its own entry, never as a chat
 * message (the backend keeps it out of the model's history). */
import { describe, expect, it } from "vitest";

import type { Message } from "../../api/types";
import { logFromMessages } from "./agentLog";

const msg = (over: Partial<Message>): Message =>
  ({ role: "user", content: "", ...over }) as Message;

describe("workflow repair request", () => {
  it("is its own entry, carrying what was asked", () => {
    const log = logFromMessages([
      msg({ role: "assistant", content: "Sure! here it is" }),
      msg({ role: "repair_request", content: "Your reply is still not valid JSON." }),
      msg({ role: "assistant", content: '{"n": 1}' }),
    ]);
    const entry = log.entries.find((e) => e.kind === "repair_request");
    expect(entry && "text" in entry && entry.text).toContain("still not valid JSON");
  });

  it("is not a chat message", () => {
    const log = logFromMessages([msg({ role: "repair_request", content: "x" })]);
    expect(log.entries.every((e) => e.kind !== "message")).toBe(true);
  });
});
