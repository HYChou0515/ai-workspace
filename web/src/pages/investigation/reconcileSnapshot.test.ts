import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../../events";
import {
  EMPTY_LOG,
  type AgentLog,
  logFromMessages,
  reconcileSnapshot,
  reduceAgent,
} from "./agentLog";

const live = (over: Partial<AgentLog> = {}): AgentLog => ({
  entries: [],
  streaming: false,
  streamingBy: null,
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

/**
 * A reload during a live turn used to render a completely idle UI: the header
 * said "your turn", the composer unlocked, the spinner was gone and — worst —
 * the cross-pod store-poll is gated on `streaming`, so the recovery that would
 * have surfaced the reply was switched off too. Meanwhile the turn was still
 * burning tokens server-side.
 *
 * A thread whose LAST message is the user's is a thread whose reply has not
 * landed. That is only a sound signal because a turn now always ends in
 * SOMETHING persisted — an answer, an error, a cancellation — whether the
 * provider hangs (the give-up deadline), the requester disconnects, the pod
 * rolls, or the store write fails.
 */
describe("hydration — is a turn still running", () => {
  it("stays in the waiting state when the reply has not landed yet", () => {
    const log = logFromMessages([
      { role: "assistant", content: "an earlier answer" },
      { role: "user", content: "and my new question", created_at: Date.now() },
    ]);
    expect(log.streaming).toBe(true);
  });

  it("is idle once the reply is there", () => {
    const log = logFromMessages([
      { role: "user", content: "q" },
      { role: "assistant", content: "a" },
    ]);
    expect(log.streaming).toBe(false);
  });

  // A turn that died is persisted as an error message, so the thread no longer
  // ends on the user and the UI stops waiting — this is what keeps the signal
  // from getting stuck on forever.
  it("is idle when the turn ended in a failure", () => {
    const log = logFromMessages([
      { role: "user", content: "q" },
      { role: "error", content: "the model gave up" },
    ]);
    expect(log.streaming).toBe(false);
  });

  it("is idle for an empty thread", () => {
    expect(logFromMessages([]).streaming).toBe(false);
  });
});

/**
 * The inference has to be bounded, or it trades one stuck state for another.
 *
 * Threads that died BEFORE a turn was guaranteed to end — a hard kill, a crash,
 * anything predating these fixes — sit in the store ending on a user message
 * forever. Without a bound, every mount of such a thread would claim "replying…"
 * and start a store-poll that can never terminate.
 *
 * A turn is bounded server-side in minutes at the very outside (the give-up
 * deadline plus retries), so a question left unanswered for far longer than that
 * is not in flight — it is abandoned.
 */
describe("hydration — the waiting state is bounded by age", () => {
  const MINUTE = 60_000;

  it("waits for a question asked moments ago", () => {
    const log = logFromMessages([
      { role: "user", content: "q", created_at: Date.now() - MINUTE },
    ]);
    expect(log.streaming).toBe(true);
  });

  it("does not wait for a question left unanswered for hours", () => {
    const log = logFromMessages([
      { role: "user", content: "q", created_at: Date.now() - 300 * MINUTE },
    ]);
    expect(log.streaming).toBe(false);
  });

  // An unstamped message is old data by definition — the timestamp predates the
  // field. Treating it as live would resurrect exactly the threads this bound
  // exists to exclude.
  it("does not wait for a message with no timestamp at all", () => {
    expect(logFromMessages([{ role: "user", content: "q" }]).streaming).toBe(false);
  });
});

/**
 * A shared item runs one turn at a time, but messages QUEUE server-side — they
 * do not cancel each other (#43). So locking every viewer's composer while
 * somebody else's turn runs takes away something the backend was happy to
 * accept, and hands a spectator a UI indistinguishable from broken: a spinner
 * they did not start, and a box they cannot type in.
 *
 * Knowing WHOSE turn is running is what makes the two cases separable.
 */
describe("whose turn is running", () => {
  it("remembers who started the turn that is streaming", () => {
    const log = reduceAgent(EMPTY_LOG, {
      type: "user_message",
      author: "bob",
      content: "bob's question",
    } as AgentEvent);
    expect(log.streaming).toBe(true);
    expect(log.streamingBy).toBe("bob");
  });

  it("forgets it once the turn ends", () => {
    const running = reduceAgent(EMPTY_LOG, {
      type: "user_message",
      author: "bob",
      content: "q",
    } as AgentEvent);
    const done = reduceAgent(running, { type: "done" } as AgentEvent);
    expect(done.streaming).toBe(false);
    expect(done.streamingBy).toBeNull();
  });

  // After a reload the thread itself says who is waiting: the trailing user
  // message is the one whose reply has not landed.
  it("recovers it from a hydrated thread", () => {
    const log = logFromMessages([
      { role: "user", content: "q", author: "carol", created_at: Date.now() },
    ]);
    expect(log.streaming).toBe(true);
    expect(log.streamingBy).toBe("carol");
  });
});
