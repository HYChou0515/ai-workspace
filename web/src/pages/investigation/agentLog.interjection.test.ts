// docs/plan-interjection.md P5 (Q9): a message sent while the AI is still
// answering says what will happen to it, sits where the AI read it, and an
// answer another person's message cut short says who cut it.
// happy-dom so the locale is pinned (see agentLog.test.ts).
// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";

import type { AgentEvent } from "../../events";
import type { Message } from "../../api/types";
import { EMPTY_LOG, drawOwnAsk, logFromMessages, reduceAgent, type AgentLog } from "./agentLog";

beforeEach(() => localStorage.setItem("ws.locale", "zh-TW"));

function fold(events: AgentEvent[], from: AgentLog = EMPTY_LOG): AgentLog {
  return events.reduce((log, ev) => reduceAgent(log, ev), from);
}

/** Ann's turn is running: she asked, the AI ran a tool and started writing. */
const annTurnRunning = (): AgentLog =>
  fold([
    { type: "user_message", author: "ann", content: "Q1", created_at: 1 },
    { type: "tool_start", call_id: "c1", name: "look", args: {} },
    { type: "tool_end", call_id: "c1", output: "lot 1" },
    { type: "message_delta", text: "Looking" },
  ]);

const userEntries = (log: AgentLog) =>
  log.entries.flatMap((e) =>
    e.kind === "message" && e.message.role === "user"
      ? [{ content: e.message.content, interjection: e.interjection }]
      : [],
  );

describe("a message its author sends while their turn runs", () => {
  it("says the AI will reply once it finishes its current step", () => {
    const log = fold([{ type: "user_message", author: "ann", content: "Q2", created_at: 2 }], annTurnRunning());
    expect(userEntries(log)).toEqual([
      { content: "Q1", interjection: undefined },
      { content: "Q2", interjection: "waiting" },
    ]);
  });

  it("says so on the sender's own screen from the moment they send it", () => {
    const sent = drawOwnAsk(annTurnRunning(), { author: "ann", content: "Q2" });
    const adopted = fold([{ type: "user_message", author: "ann", content: "Q2", created_at: 2 }], sent);
    expect(userEntries(sent).at(-1)).toEqual({ content: "Q2", interjection: "waiting" });
    expect(userEntries(adopted).at(-1)).toEqual({ content: "Q2", interjection: "waiting" });
  });

  it("a turn's FIRST message carries no such note", () => {
    const sent = drawOwnAsk(EMPTY_LOG, { author: "ann", content: "Q1" });
    const adopted = fold([{ type: "user_message", author: "ann", content: "Q1", created_at: 1 }], sent);
    expect(userEntries(adopted)).toEqual([{ content: "Q1", interjection: undefined }]);
  });

  it("moves to where the AI read it, and the rest of the answer is a new answer below it", () => {
    const log = fold(
      [
        { type: "user_message", author: "ann", content: "Q2", created_at: 2 },
        { type: "message_delta", text: " further" },
        { type: "tool_start", call_id: "c2", name: "look", args: {} },
        { type: "tool_end", call_id: "c2", output: "lot 2" },
        { type: "interjected", author: "ann", created_at: 2 },
        { type: "message_delta", text: "Both answered" },
      ],
      annTurnRunning(),
    );
    const shape = log.entries.map((e) =>
      e.kind === "message" ? `${e.message.role}:${e.message.content}` : e.kind,
    );
    expect(shape).toEqual([
      "user:Q1",
      "tool_call",
      "assistant:Looking further",
      "tool_call",
      "user:Q2",
      "assistant:Both answered",
    ]);
    expect(userEntries(log).at(-1)).toEqual({ content: "Q2", interjection: "read" });
  });

  it("when the turn ends without reading it, says the next turn answers it", () => {
    const log = fold(
      [
        { type: "user_message", author: "ann", content: "Q2", created_at: 2 },
        { type: "done" },
      ],
      annTurnRunning(),
    );
    expect(userEntries(log).at(-1)).toEqual({ content: "Q2", interjection: "queued" });
    // …and once that next turn is answering, the note has done its job.
    const answering = fold([{ type: "message_delta", text: "A2" }], log);
    expect(userEntries(answering).at(-1)).toEqual({ content: "Q2", interjection: undefined });
  });

  it("a note that was read stops once the turn that read it has ended", () => {
    const log = fold(
      [
        { type: "user_message", author: "ann", content: "Q2", created_at: 2 },
        { type: "interjected", author: "ann", created_at: 2 },
        { type: "message_delta", text: "Both" },
        { type: "done" },
      ],
      annTurnRunning(),
    );
    expect(userEntries(log).at(-1)).toEqual({ content: "Q2", interjection: undefined });
  });
});

describe("a turn stopped after reading a message", () => {
  // §9 C9: a stopped turn answered nothing it read — the message goes back to
  // the queue and the next turn answers it, which is what the note must say.
  it.each([
    ["Stop", { type: "run_cancelled" } as AgentEvent],
    ["a takeover", { type: "run_cancelled", by: "bob" } as AgentEvent],
  ])("after %s, says the next turn answers it", (_name, ending) => {
    const log = fold(
      [
        { type: "user_message", author: "ann", content: "Q2", created_at: 2 },
        { type: "interjected", author: "ann", created_at: 2 },
        ending,
      ],
      annTurnRunning(),
    );
    expect(userEntries(log).at(-1)).toEqual({ content: "Q2", interjection: "queued" });
  });
});

describe("another person's message", () => {
  it("is not marked as waiting: it takes the turn over", () => {
    const log = fold([{ type: "user_message", author: "bob", content: "Q2", created_at: 2 }], annTurnRunning());
    expect(userEntries(log).at(-1)).toEqual({ content: "Q2", interjection: undefined });
  });

  it("ends the running answer with a banner naming who interrupted", () => {
    const log = fold(
      [
        { type: "user_message", author: "bob", content: "Q2", created_at: 2 },
        { type: "run_cancelled", by: "bob" },
      ],
      annTurnRunning(),
    );
    const banners = log.entries.flatMap((e) => (e.kind === "banner" ? [e.text] : []));
    expect(banners).toEqual(["已被 bob 的訊息中斷。"]);
  });

  it("reads the same after a reload, so the live and stored copies are one event", () => {
    const stored: Message[] = [
      { role: "user", content: "Q1", author: "ann", created_at: 1 },
      {
        role: "error",
        content: "The previous response was interrupted by a new message.",
        error_kind: "interrupted",
        author: "bob",
        created_at: 3,
      },
      { role: "user", content: "Q2", author: "bob", created_at: 2 },
    ];
    const banners = logFromMessages(stored).entries.flatMap((e) => (e.kind === "banner" ? [e.text] : []));
    expect(banners).toEqual(["已被 bob 的訊息中斷。"]);
  });

  it("a plain Stop still reads as cancelled", () => {
    const log = fold([{ type: "run_cancelled" }], annTurnRunning());
    const banners = log.entries.flatMap((e) => (e.kind === "banner" ? [e.text] : []));
    expect(banners).toEqual(["已取消。"]);
  });
});
