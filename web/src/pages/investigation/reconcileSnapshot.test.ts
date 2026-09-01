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
  rateLimited: null,
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

  it("keeps a banner raised mid-turn when that same turn ends", () => {
    // A turn's messages are stamped AS THEY ARE PRODUCED — `_TurnReducer` opens a
    // fresh assistant message after every tool call — so a banner raised early in
    // a turn is necessarily older than that turn's own later messages. Anything
    // that decides staleness by comparing timestamps therefore deletes it at the
    // turn's own terminal refetch. `parse error: …` is #76 transparency and its
    // emitter says "ALWAYS push a banner — never silently drop, or the retry is
    // invisible"; it has to survive the turn it belongs to.
    const prev = live({
      entries: [
        msg("user", "q"),
        msg("assistant", "thinking"),
        { kind: "banner", at: 1200, text: "parse error: the model sent bad JSON" },
      ],
    });

    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q", created_at: 1000 },
        { role: "assistant", content: "thinking", created_at: 1100 },
        // …the turn continued after the banner and persisted more of itself.
        { role: "assistant", content: "the answer", created_at: 1400 },
      ],
    });

    expect(next.entries.some((e) => e.kind === "banner")).toBe(true);
  });

  it("stops carrying a banner once the conversation has moved past it", () => {
    // A carried banner is re-attached at the END of the fresh snapshot, so one
    // left over from an earlier turn does not merely linger — it MOVES, landing
    // under the newest answer as if it were about that turn. "已達回合上限,對話
    // 已停止" then shows beneath a turn that finished perfectly well, which is
    // the report: it never goes away.
    //
    // Staleness is measured in TURNS, not milliseconds: the banner records how
    // many user messages the thread had when it was raised, and a thread that has
    // gained one has moved on to a turn the banner is not about. No clocks — the
    // banner's would be the browser's and the messages' the server's.
    const prev = live({
      entries: [
        msg("user", "q1"),
        msg("assistant", "half"),
        { kind: "banner", at: 3, text: "已達回合上限（10），對話已停止。" },
      ],
    });

    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q1", created_at: 1 },
        { role: "assistant", content: "half", created_at: 2 },
        { role: "user", content: "q2", created_at: 4 },
        { role: "assistant", content: "a proper answer", created_at: 5 },
      ],
    });

    expect(next.entries.some((e) => e.kind === "banner")).toBe(false);
    expect(
      next.entries.some((e) => e.kind === "message" && e.message.content === "a proper answer"),
    ).toBe(true);
  });

  it("still carries a banner about the turn the thread ends on", () => {
    // The counterpart: a stop that IS the latest thing to have happened must
    // survive the re-hydrate, or a stopped turn reads as merely finished.
    // Note the ordering a real turn produces: the banner is stamped BEFORE the
    // turn's own later messages, which is exactly what a timestamp rule gets
    // wrong. Counting turns is indifferent to it.
    const prev = live({
      entries: [msg("user", "q"), { kind: "banner", at: 1, text: "已取消。" }],
    });

    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q", created_at: 1 },
        { role: "assistant", content: "partial", created_at: 2 },
      ],
    });

    expect(next.entries.some((e) => e.kind === "banner" && e.text === "已取消。")).toBe(true);
  });

  it("dates a banner even when the store it first meets is behind", () => {
    // The store-behind early return happens BEFORE the turn is assigned, and the
    // race is documented, not hypothetical: the terminal event is published
    // before the turn is persisted, so the refetch it triggers can legitimately
    // arrive early. A banner that meets that snapshot stays undated, and then
    // adopts whatever turn the thread has reached by its NEXT re-hydrate — one
    // turn too late, which is the whole reported symptom again.
    const prev = live({
      entries: [
        msg("user", "q1"),
        msg("assistant", "half"),
        { kind: "banner", at: 3, text: "已達回合上限（10），對話已停止。" },
      ],
    });

    // First re-hydrate: the store has not caught up, so the screen is kept.
    const behind = reconcileSnapshot(prev, {
      messages: [{ role: "user", content: "q1", created_at: 1 }],
    });
    expect(behind.entries.some((e) => e.kind === "banner")).toBe(true);

    // Second: a whole new turn has since been persisted. The banner is about the
    // first one and must not follow the conversation down.
    const later = reconcileSnapshot(behind, {
      messages: [
        { role: "user", content: "q1", created_at: 1 },
        { role: "assistant", content: "half", created_at: 2 },
        { role: "user", content: "q2", created_at: 4 },
        { role: "assistant", content: "a proper answer", created_at: 5 },
      ],
    });

    expect(later.entries.some((e) => e.kind === "banner")).toBe(false);
  });

  it("drops a banner the next question has already been asked under", () => {
    // The other half of the rule: the next turn can reach the log through the
    // STREAM (its `user_message` folds in under the banner) long before the store
    // knows about it. Reading only the snapshot's turn count misses that, and
    // misses it hardest where the count is uninformative — a log that never held
    // a user message of its own, where the "cannot place it" escape hatch would
    // otherwise keep the banner no matter how far the conversation had run on.
    const prev = live({
      entries: [
        { kind: "banner", at: 1, text: "已取消。" },
        msg("user", "and now something else"),
        msg("assistant", "a fresh answer"),
      ],
    });

    // The store has caught up — anything less takes the store-behind early
    // return, where the log is kept whole and the carry rule never runs.
    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "and now something else", created_at: 9 },
        { role: "assistant", content: "a fresh answer", created_at: 10 },
      ],
    });

    expect(next.entries.some((e) => e.kind === "banner")).toBe(false);
  });

  it("says a stop ONCE, not once live and again from the store", () => {
    // Reproduced in the running app: one press of Stop rendered three lines —
    // the live 「已取消。」 banner, the backend's persisted `role:"error"`
    // message ("The previous response was interrupted.", English, in a zh-TW
    // UI), and a composer hint saying it a third time. The de-dupe here compares
    // TEXT, so two wordings of one event never collapse.
    //
    // The persisted message carries `error_kind`, which is the machine-readable
    // half: the backend says WHAT happened, the UI says how to word it. Worded
    // from the same source, the two are the same banner and one of them goes.
    const prev = live({
      entries: [msg("user", "q"), { kind: "banner", at: 5, text: "已取消。" }],
    });

    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q", created_at: 1 },
        {
          role: "error",
          content: "The previous response was interrupted.",
          error_kind: "cancelled",
          created_at: 6,
        },
      ],
    });

    expect(next.entries.filter((e) => e.kind === "banner")).toHaveLength(1);
    expect(next.entries.some((e) => e.kind === "banner" && e.text === "已取消。")).toBe(true);
  });

  it("KNOWN GAP: a step-limited turn still says it twice, once in English", () => {
    // The cancel case above is fixed by wording the stored message from its
    // `error_kind`. `max_turns` is not, and this pins what that costs rather
    // than leaving it for someone to rediscover: its persisted text carries the
    // step count inline, so re-rendering it in the user's language would mean
    // parsing a number back out of an English sentence, and the alternatives
    // are dropping the count from both sides or adding a field to the stored
    // message. Neither was reported, so neither was chosen here.
    const prev = live({
      entries: [
        msg("user", "q"),
        { kind: "banner", at: 5, text: "已達回合上限（12），對話已停止。" },
      ],
    });

    const next = reconcileSnapshot(prev, {
      messages: [
        { role: "user", content: "q", created_at: 1 },
        {
          role: "error",
          content: "The agent stopped after reaching its step limit (12).",
          error_kind: "max_turns",
          created_at: 6,
        },
      ],
    });

    expect(next.entries.filter((e) => e.kind === "banner")).toHaveLength(2);
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
