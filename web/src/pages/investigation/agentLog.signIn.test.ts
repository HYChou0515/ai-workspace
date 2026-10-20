/** `docs/plan-env-token-refresh.md` D6: a sign-in whose refresh token is dead
 * is told in the thread — a persisted platform message (role `sign_in`) that
 * reaches the screen as its own entry, carrying which sign-in and which layer,
 * and never becomes a chat message the model is replayed. */
import { describe, expect, it } from "vitest";

import type { Message } from "../../api/types";
import { logFromMessages } from "./agentLog";

const msg = (over: Partial<Message>): Message => ({ role: "user", content: "", ...over }) as Message;

describe("sign-in message", () => {
  it("becomes its own entry naming the sign-in and the layer", () => {
    const log = logFromMessages([
      msg({ role: "user", content: "q" }),
      msg({
        role: "sign_in",
        content: "Your sign-in to ERP has expired. Sign in again.",
        sign_in_provider: "erp",
        sign_in_layer: "personal",
        created_at: 5,
      }),
    ]);

    const entry = log.entries.find((e) => e.kind === "sign_in");
    expect(entry).toEqual({
      kind: "sign_in",
      text: "Your sign-in to ERP has expired. Sign in again.",
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
