import { describe, expect, it } from "vitest";

import { type AgentLog, logFromMessages, reconcileSnapshot } from "./agentLog";

const live = (over: Partial<AgentLog> = {}): AgentLog => ({
  entries: [],
  streaming: false,
  error: null,
  metrics: null,
  failover: null,
  restore: null,
  ...over,
});

const msg = (role: "user" | "assistant", content: string) =>
  ({ kind: "message", message: { role, content } }) as AgentLog["entries"][number];

/**
 * The persisted thread is written ONCE, at turn end. Mid-turn it holds nothing
 * of the answer being streamed — so replacing the log with it wholesale deletes
 * exactly what the user is reading. That is the "the response disappears"
 * symptom, and it fires hardest when a turn is stuck: a long silence is when a
 * connection gets cut and a re-hydrate is triggered.
 */
describe("reconcileSnapshot", () => {
  it("keeps the streamed answer while the store is still behind", () => {
    const prev = live({
      entries: [msg("user", "q"), msg("assistant", "half an answer so far")],
      streaming: true,
    });
    // The store only has the user turn — the reply is not persisted yet.
    const next = reconcileSnapshot(prev, { messages: [{ role: "user", content: "q" }] });

    expect(next.entries).toHaveLength(2);
    expect(next.entries[1]).toMatchObject({ message: { content: "half an answer so far" } });
  });

  it("adopts the persisted thread once it has caught up", () => {
    // The persisted version is the one that carries BE-attached citations, so
    // once it is no smaller it must win.
    const prev = live({ entries: [msg("user", "q"), msg("assistant", "draft")] });
    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q" },
        { role: "assistant", content: "final, with citations" },
      ],
    });

    expect(next.entries[1]).toMatchObject({ message: { content: "final, with citations" } });
  });

  it("carries the turn's error forward instead of nulling it", () => {
    // `logFromMessages` hard-resets `error: null`, so re-snapshotting on the
    // `error` event destroyed the very message that explained the failure —
    // the red box showed for one frame and vanished.
    const prev = live({ entries: [msg("user", "q")], error: "provider refused the request" });
    const next = reconcileSnapshot(prev, { messages: [{ role: "user", content: "q" }] });

    expect(next.error).toBe("provider refused the request");
  });

  it("keeps a live-only banner the persisted thread cannot represent", () => {
    // "cancelled" / "max turns" / "repetition" are stream banners, not messages;
    // a snapshot dropped them, so a stopped turn ended up looking merely finished.
    const prev = live({
      entries: [msg("user", "q"), { kind: "banner", text: "已取消" }],
    });
    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q" },
        { role: "assistant", content: "partial" },
      ],
    });

    expect(next.entries.some((e) => e.kind === "banner" && e.text === "已取消")).toBe(true);
    expect(next.entries.some((e) => e.kind === "message" && e.message.content === "partial")).toBe(
      true,
    );
  });

  it("does not duplicate a banner the persisted thread already carries", () => {
    const prev = live({ entries: [{ kind: "banner", text: "turn failed" }] });
    // role:"error" hydrates as the same banner.
    const next = reconcileSnapshot(prev, { messages: [{ role: "error", content: "turn failed" }] });

    expect(next.entries.filter((e) => e.kind === "banner")).toHaveLength(1);
  });

  it("is a plain snapshot when there is nothing live to protect", () => {
    const thread = { messages: [{ role: "user" as const, content: "q" }] };
    expect(reconcileSnapshot(live(), thread).entries).toEqual(logFromMessages(thread.messages).entries);
  });
});
