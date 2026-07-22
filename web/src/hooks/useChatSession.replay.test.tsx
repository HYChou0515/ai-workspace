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
});
