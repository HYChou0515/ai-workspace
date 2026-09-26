/**
 * #847 P7: markings sent with a message are chips on it — drawn by the sender
 * at once, then replaced by what the server recorded (a refused one included).
 */
import { describe, expect, it } from "vitest";

import { drawOwnAsk, EMPTY_LOG, reduceAgent } from "./agentLog";

const sent = { name: "fail", path: "/.markings/fail.json", counts: { lot: 2 }, error: null };
const refused = { name: "big", path: "", counts: { lot: 9 }, error: "workspace is full" };

describe("markings on a user message", () => {
  it("the sender's own draw carries the chips it sent", () => {
    const log = drawOwnAsk(EMPTY_LOG, {
      author: "u",
      content: "why?",
      markings: [{ name: "fail", path: "", counts: { lot: 2 } }],
    });
    const last = log.entries.at(-1)!;
    expect(last.kind === "message" && last.message.markings?.map((m) => m.name)).toEqual([
      "fail",
    ]);
  });

  it("the broadcast's recorded markings replace the sender's draft chips", () => {
    const drawn = drawOwnAsk(EMPTY_LOG, {
      author: "u",
      content: "why?",
      markings: [
        { name: "fail", path: "", counts: { lot: 2 } },
        { name: "big", path: "", counts: { lot: 9 } },
      ],
    });
    const after = reduceAgent(drawn, {
      type: "user_message",
      author: "u",
      content: "why?",
      created_at: 100,
      markings: [sent, refused],
    });
    const asks = after.entries.filter((e) => e.kind === "message" && e.message.role === "user");
    expect(asks).toHaveLength(1);
    const [ask] = asks;
    expect(ask!.kind === "message" && ask!.message.markings).toEqual([sent, refused]);
  });

  it("another viewer draws the chips from the broadcast", () => {
    const after = reduceAgent(EMPTY_LOG, {
      type: "user_message",
      author: "alice",
      content: "why?",
      created_at: 100,
      markings: [sent],
    });
    const last = after.entries.at(-1)!;
    expect(last.kind === "message" && last.message.markings).toEqual([sent]);
  });
});
