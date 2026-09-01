// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent } from "../events";
import { QueryWrap } from "../test/queryWrapper";
import { type BroadcastChatTransport, useChatSession } from "./useChatSession";

vi.mock("../api", () => ({ api: { getCurrentUser: vi.fn().mockResolvedValue("tester") } }));

/**
 * Same-pod reconnect replay (#43). A dropped SSE stream used to lose the events
 * emitted during the gap — even on reconnect to the same pod — because the
 * broadcast has no replay buffer. Now each event carries a `seq`; the hook
 * tracks the max it has seen and, on RECONNECT, asks the server to resume from
 * there (`?since=`), so the buffered gap is replayed. A fresh connect asks for
 * nothing (no replay).
 */

const THREAD = {
  messages: [{ role: "user" as const, content: "q", created_at: Date.now() }],
};

/** A broadcast event carrying the transport-level `seq` (not a domain field). */
function ev(text: string, seq?: number): AgentEvent {
  return { type: "message_delta", text, ...(seq !== undefined ? { seq } : {}) } as AgentEvent;
}

function transport(over: Partial<BroadcastChatTransport> = {}): BroadcastChatTransport {
  return {
    threadKey: "c1",
    queryKey: ["chat", "c1"],
    filesKey: ["files", "it"],
    getThread: vi.fn().mockResolvedValue(THREAD),
    subscribe: async function* () {
      await new Promise<void>(() => {});
    },
    post: vi.fn().mockResolvedValue(undefined),
    requestCancel: vi.fn(),
    undoTurns: vi.fn().mockResolvedValue(undefined),
    addMention: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
}

const render = (t: BroadcastChatTransport, pollMs = 60_000) =>
  renderHook(() => useChatSession(t, pollMs), { wrapper: QueryWrap });

afterEach(() => vi.restoreAllMocks());

describe("useChatSession — reconnect replay", () => {
  it("resumes from the last seq it saw; a fresh connect asks for nothing", async () => {
    const sinces: (number | undefined)[] = [];
    let attempt = 0;
    render(
      transport({
        subscribe: async function* (_signal: AbortSignal, since?: number) {
          sinces.push(since);
          attempt += 1;
          if (attempt === 1) {
            yield ev("first half", 5); // client sees up to seq 5, then the stream drops
            throw new Error("stream failed: 504");
          }
          await new Promise<void>(() => {}); // healthy after reconnect
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    expect(sinces[0]).toBeUndefined(); // fresh connect: no replay
    await waitFor(() => expect(sinces[1]).toBe(5), { timeout: 4000 }); // reconnect: resume from 5
  });

  const hasBanner = (entries: { kind: string }[]) => entries.some((e) => e.kind === "banner");

  it("a contiguous replay fills the hole — no 'missing piece' banner", async () => {
    let attempt = 0;
    const { result } = render(
      transport({
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("first half", 5); // mid-answer; seen up to seq 5, then drop
            throw new Error("stream failed: 504");
          }
          yield ev("second half", 6); // reconnect resumes from 5 → seq 6 is contiguous
          await new Promise<void>(() => {});
        },
      }),
    );

    // The reconnect delivered the very next seq, so the answer is whole again and
    // the transient "少了一段" banner must be gone.
    await waitFor(
      () => {
        const answer = result.current.log.entries.find(
          (e) => e.kind === "message" && e.message.role === "assistant",
        );
        expect(answer?.kind === "message" && answer.message.content).toContain("second half");
        expect(hasBanner(result.current.log.entries)).toBe(false);
      },
      { timeout: 4000 },
    );
  });

  it("keeps the banner when the gap outran the buffer (a real hole)", async () => {
    let attempt = 0;
    const { result } = render(
      transport({
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("first half", 5);
            throw new Error("stream failed: 504");
          }
          yield ev("much later", 9); // resumes from 5 but next is 9 → seqs 6-8 lost
          await new Promise<void>(() => {});
        },
      }),
    );

    await waitFor(() => expect(hasBanner(result.current.log.entries)).toBe(true), { timeout: 4000 });
  });

  /** A store that behaves like the real one: once the turn is done, the thread
   * it hands back CONTAINS the answer. The static thread the tests above share
   * never grows, which is not a backend this product has — and against it the
   * hook's own hydration wipes the streamed answer, so `midAnswer` reads false
   * and the gap banner is never even added. A double that cannot reproduce the
   * bug reports the fix as already done. */
  const persistingThread = (finished: () => boolean, answer: string) =>
    vi.fn(async () => ({
      messages: finished()
        ? [
            ...THREAD.messages,
            { role: "assistant" as const, content: answer, created_at: 2 },
          ]
        : THREAD.messages,
    }));

  it("drops the banner when the turn it interrupted finishes", async () => {
    // The hole is a claim about ONE turn. When that turn ends the thread is
    // re-read and what is on screen is the stored answer, so the claim describes
    // nothing — whatever the seq numbers said. A banner that outlives the thing
    // it was about is the false alarm: the user is told they lost something
    // while looking at the complete answer.
    let attempt = 0;
    let finished = false;
    const { result } = render(
      transport({
        getThread: persistingThread(() => finished, "first half second half"),
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("first half", 5);
            throw new Error("stream failed: 504");
          }
          yield ev(" second half", 99); // NOT contiguous — another pod's numbering
          finished = true;
          yield { type: "done" } as AgentEvent;
          await new Promise<void>(() => {});
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await waitFor(() => expect(hasBanner(result.current.log.entries)).toBe(false), {
      timeout: 4000,
    });
  });

  it("forgets a finished turn, so a later idle drop says nothing", async () => {
    // The terminal branch clears the in-flight flag. Without that line the flag
    // stays armed after a turn ends, and the NEXT drop — with nothing running —
    // raises a banner about a turn that finished long ago. The store-based
    // retraction hides this whenever the thread ends on a fresh answer, so this
    // drives the case it cannot cover: the store never catches up, so nothing
    // else can retract what the flag wrongly armed.
    let attempt = 0;
    const { result } = render(
      transport({
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("the whole answer", 5);
            yield { type: "done" } as AgentEvent;
            return; // the server closes the stream — a drop, with the turn over
          }
          await new Promise<void>(() => {}); // reconnected, and quiet ever after
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(1), { timeout: 4000 });
    await new Promise((r) => setTimeout(r, 200));
    expect(hasBanner(result.current.log.entries)).toBe(false);
  });

  it("drops the banner when the store shows the turn ended during the gap", async () => {
    // The terminal event cannot be relied on to retract it: a pod with no
    // subscriber buffers nothing, so a turn that finishes while the viewer is
    // away never announces itself to the reconnected stream. Without this the
    // banner sits under the COMPLETE answer — the whole answer, pulled from the
    // store by the reconnect's own re-hydrate — for the rest of the session.
    let attempt = 0;
    let finished = false;
    const { result } = render(
      transport({
        getThread: persistingThread(() => finished, "first half and the rest of it"),
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("first half", 5);
            finished = true; // the turn completes server-side during the outage
            throw new Error("stream failed: 504");
          }
          await new Promise<void>(() => {}); // the reconnect hears nothing at all
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await waitFor(() => expect(hasBanner(result.current.log.entries)).toBe(false), {
      timeout: 4000,
    });
  });

  it("keeps the banner when the thread merely ENDS on an answer", async () => {
    // A tail that is not a user message does not mean the turn ended. A workflow
    // chat never persists a user message at all — from the second node on, the
    // tail is the previous node's reply while the next one streams — and a #624
    // `notice` or a human `mention` can be the tail at any moment. Retracting on
    // that shape deletes a banner about a hole that is still open, which is the
    // opposite failure and the harder one to notice.
    let attempt = 0;
    const settled = {
      messages: [
        { role: "user" as const, content: "q", created_at: 1 },
        { role: "assistant" as const, content: "node 1 replied", created_at: 2 },
      ],
    };
    const { result } = render(
      transport({
        // The store is UNCHANGED across the outage: nothing new landed.
        getThread: vi.fn(async () => settled),
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("node 2 is writing", 5);
            throw new Error("stream failed: 504");
          }
          yield ev("resumed elsewhere", 99); // non-contiguous: the hole is real
          await new Promise<void>(() => {});
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await new Promise((r) => setTimeout(r, 200));
    expect(hasBanner(result.current.log.entries)).toBe(true);
  });

  it("does not carry 'a turn was running' across a thread switch", async () => {
    // The sibling refs (`maxSeqRef`, `seenIdsRef`) are reset when the thread
    // changes, for the same reason: a value about the chat you just left is
    // poison in the one you just opened. Left behind, chat A's running turn makes
    // chat B's first drop claim a hole in a conversation that never ran anything
    // — and with no turn in B, nothing will ever retract it.
    const idle = transport({ threadKey: "B", queryKey: ["chat", "B"] });
    let attempt = 0;
    const busy = transport({
      threadKey: "A",
      queryKey: ["chat", "A"],
      subscribe: async function* (_signal: AbortSignal) {
        attempt += 1;
        yield ev("mid answer", 1); // a turn IS running on A
        await new Promise<void>(() => {});
      },
    });

    const { result, rerender } = renderHook(({ t }) => useChatSession(t, 60_000), {
      wrapper: QueryWrap,
      initialProps: { t: busy },
    });
    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(1), { timeout: 4000 });

    // Switch to B, whose stream then drops without ever delivering a turn event.
    let bAttempt = 0;
    rerender({
      t: transport({
        threadKey: "B",
        queryKey: ["chat", "B"],
        subscribe: async function* (_signal: AbortSignal) {
          bAttempt += 1;
          if (bAttempt === 1) throw new Error("stream failed: 504");
          await new Promise<void>(() => {});
        },
        getThread: idle.getThread,
      }),
    });

    await waitFor(() => expect(bAttempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await new Promise((r) => setTimeout(r, 200));
    expect(hasBanner(result.current.log.entries)).toBe(false);
  });

  it("arms on the question itself, not only on the first token", async () => {
    // The window between "a turn was admitted" and its first delta is the model's
    // time-to-first-token — seconds on a local model — and a stream lost inside it
    // loses the whole answer with nothing yet on screen. `user_message` is
    // broadcast only after admission and persistence, so unlike the other
    // non-output events it cannot fire without a turn behind it.
    let attempt = 0;
    const { result } = render(
      transport({
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield { type: "user_message", content: "q", author: "tester" } as AgentEvent;
            throw new Error("stream failed: 504"); // dropped before the first token
          }
          yield ev("resumed elsewhere", 99);
          await new Promise<void>(() => {});
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await waitFor(() => expect(hasBanner(result.current.log.entries)).toBe(true), {
      timeout: 4000,
    });
  });

  it("a broadcast that is not turn progress does not arm the banner", async () => {
    // `file_changed` is published on any workspace write — a person saving a file
    // in the editor, an entity commit — on the item's own engine key, with no turn
    // anywhere. Treating "not presence" as "a turn is running" let an idle window
    // that blinked claim a missing piece, and since no turn is running, no
    // terminal event ever comes to take it back. The same door as the original
    // bug, one event type over. `todos_updated` and `goal_updated` are the same.
    let attempt = 0;
    const { result } = render(
      transport({
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield { type: "file_changed", path: "/notes.md" } as unknown as AgentEvent;
            throw new Error("stream failed: 504");
          }
          await new Promise<void>(() => {});
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await new Promise((r) => setTimeout(r, 200));
    expect(hasBanner(result.current.log.entries)).toBe(false);
  });

  it("says nothing when the drop caught no turn in flight", async () => {
    // A finished chat that blips. Nothing was being written, so nothing can be
    // missing — and no further event will ever arrive, so a banner added here is
    // permanent: the retraction only ever runs inside the event loop. This is the
    // common case, because "the last entry is an assistant message" is the
    // resting state of every chat that has ever been answered.
    let attempt = 0;
    let finished = false;
    const { result } = render(
      transport({
        getThread: persistingThread(() => finished, "the whole answer"),
        subscribe: async function* (_signal: AbortSignal) {
          attempt += 1;
          if (attempt === 1) {
            yield ev("the whole answer", 5);
            finished = true;
            yield { type: "done" } as AgentEvent; // the turn ENDED before the drop
            throw new Error("stream failed: 504");
          }
          await new Promise<void>(() => {}); // reconnected, and quiet ever after
        },
      }),
    );

    await waitFor(() => expect(attempt).toBeGreaterThanOrEqual(2), { timeout: 4000 });
    await new Promise((r) => setTimeout(r, 200)); // let any late banner land
    expect(hasBanner(result.current.log.entries)).toBe(false);
  });
});
