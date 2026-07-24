/** #624: the context notice must reach the screen and stay out of the model.
 *
 * A cut the user is never told about is indistinguishable from the model simply
 * being forgetful — that is the whole reason this issue exists. The notice is
 * persisted so it survives a reload, which means it also has to behave like
 * every other non-dialogue entry: countable as content, never replayed to the
 * LLM (the backend's `history_items` drops it; these lock the FE half).
 */
import { describe, expect, it } from "vitest";

import type { Message } from "../../api/types";
import { logFromMessages } from "./agentLog";

const msg = (over: Partial<Message>): Message =>
  ({ role: "user", content: "", ...over }) as Message;

describe("context-trim notice", () => {
  it("renders a persisted notice as its own entry", () => {
    const log = logFromMessages([
      msg({ role: "user", content: "q" }),
      msg({ role: "notice", content: "較早的 12 則訊息…不會被讀到" }),
      msg({ role: "assistant", content: "a" }),
    ]);

    const notice = log.entries.find((e) => e.kind === "notice");
    expect(notice).toBeDefined();
    expect(notice && "text" in notice && notice.text).toContain("不會被讀到");
  });

  it("does not turn the notice into a chat message", () => {
    const log = logFromMessages([msg({ role: "notice", content: "cut" })]);

    expect(log.entries.every((e) => e.kind !== "message")).toBe(true);
  });

  it("keeps the notice out of the trailing-user-message streaming heuristic", () => {
    // A notice written after the user's message must not make the thread look
    // like it is still awaiting a reply, nor mask that it is.
    const log = logFromMessages([
      msg({ role: "user", content: "q" }),
      msg({ role: "assistant", content: "a" }),
      msg({ role: "notice", content: "cut" }),
    ]);

    expect(log.streaming).toBe(false);
  });
});
