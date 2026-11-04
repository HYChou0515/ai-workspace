/** `docs/plan-env-token-refresh.md` D6: a sign-in whose refresh token is dead
 * is told in the thread — a persisted platform message (role `sign_in`) that
 * reaches the screen as its own entry, carrying which sign-in and which layer,
 * and never becomes a chat message the model is replayed. */
import { describe, expect, it } from "vitest";

import type { Message } from "../../api/types";
import type { AgentEvent } from "../../events";
import { EMPTY_LOG, logFromMessages, reconcileSnapshot, reduceAgent } from "./agentLog";

const msg = (over: Partial<Message>): Message => ({ role: "user", content: "", ...over }) as Message;

describe("sign-in message", () => {
  it("becomes its own entry naming the sign-in and the layer", () => {
    const log = logFromMessages([
      msg({ role: "user", content: "q" }),
      msg({
        role: "sign_in",
        content: "The sign-in to ERP has expired. Sign in again.",
        sign_in_provider: "erp",
        sign_in_layer: "personal",
        created_at: 5,
      }),
    ]);

    const entry = log.entries.find((e) => e.kind === "sign_in");
    expect(entry).toEqual({
      kind: "sign_in",
      text: "The sign-in to ERP has expired. Sign in again.",
      provider: "erp",
      layer: "personal",
      at: 5,
    });
  });

  it("is not a chat message", () => {
    const log = logFromMessages([msg({ role: "sign_in", content: "x", sign_in_provider: "erp" })]);
    expect(log.entries.every((e) => e.kind !== "message")).toBe(true);
  });
});

describe("sign-in notice, live", () => {
  const live: AgentEvent = {
    type: "sign_in_expired",
    provider: "erp",
    layer: "personal",
    text: "The sign-in to ERP has expired. Sign in again.",
    created_at: 7,
  };

  it("is drawn the moment it is announced, under the question", () => {
    const asked = reduceAgent(EMPTY_LOG, { type: "user_message", author: "amy", content: "q", created_at: 7 }, 7);
    const log = reduceAgent(asked, live, 8);
    expect(log.entries.map((e) => e.kind)).toEqual(["message", "sign_in"]);
    expect(log.entries[1]).toEqual({
      kind: "sign_in",
      text: "The sign-in to ERP has expired. Sign in again.",
      provider: "erp",
      layer: "personal",
      at: 7,
    });
  });

  it("is not drawn twice when the thread already showed it", () => {
    const shown = logFromMessages([
      msg({ role: "user", content: "q", created_at: 7 }),
      msg({ role: "sign_in", content: live.type === "sign_in_expired" ? live.text : "", sign_in_provider: "erp", sign_in_layer: "personal", created_at: 7 }),
    ]);
    const log = reduceAgent(shown, live, 8);
    expect(log.entries.filter((e) => e.kind === "sign_in")).toHaveLength(1);
  });

  it("survives the end of a turn whose live log is ahead of the store (the demo's case)", () => {
    // Live: the question, the notice, two streamed answers; store: the same
    // answers persisted as ONE message — behind by count, so the screen stays.
    let log = reduceAgent(EMPTY_LOG, { type: "user_message", author: "amy", content: "q", created_at: 7 }, 7);
    log = reduceAgent(log, live, 8);
    log = reduceAgent(log, { type: "message_delta", text: "Checking…" }, 9);
    log = reduceAgent(log, { type: "tool_start", call_id: "c1", name: "summarise", args: {} }, 10);
    log = reduceAgent(log, { type: "tool_end", call_id: "c1", output: "ok" }, 11);
    log = reduceAgent(log, { type: "message_delta", text: "The tool ran." }, 12);
    log = reduceAgent(log, { type: "done" }, 13);
    const after = reconcileSnapshot(log, {
      messages: [
        msg({ role: "user", content: "q", created_at: 7 }),
        msg({ role: "sign_in", content: "x", sign_in_provider: "erp", sign_in_layer: "personal", created_at: 7 }),
        msg({ role: "assistant", content: "Checking…The tool ran.", created_at: 12 }),
      ],
    });
    expect(after.entries.some((e) => e.kind === "sign_in")).toBe(true);
  });
});
