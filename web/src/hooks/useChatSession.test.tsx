// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent } from "../events";
import { QueryWrap } from "../test/queryWrapper";
import { type BroadcastChatTransport, useChatSession } from "./useChatSession";

vi.mock("../api", () => ({ api: { getCurrentUser: vi.fn().mockResolvedValue("tester") } }));

/**
 * The broadcast state machine is exercised through `useAgent` / `useItemChat`
 * elsewhere, which leaves its own branches only incidentally covered. These
 * drive it DIRECTLY through a fake transport so each branch is reached on
 * purpose — this is the body both WorkItem chats now share, so a hole here is a
 * hole in every chat at once.
 */

const THREAD = { messages: [{ role: "user" as const, content: "q" }] };

function fakeTransport(over: Partial<BroadcastChatTransport> = {}): BroadcastChatTransport {
  return {
    threadKey: "c1",
    queryKey: ["chat", "c1"],
    filesKey: ["files", "it"],
    getThread: vi.fn().mockResolvedValue(THREAD),
    // Hangs unless overridden — the steady-state subscription.
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

describe("useChatSession", () => {
  it("hydrates from the persisted thread", async () => {
    const { result } = render(fakeTransport());
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));
  });

  // `file_changed` is a workspace side effect, not a turn event: it must refresh
  // the file tree and NOT be folded into the conversation, or a human's edit
  // would appear as a chat entry.
  it("routes file_changed to the file tree instead of the log", async () => {
    const events: AgentEvent[] = [{ type: "file_changed", path: "/a.txt" } as AgentEvent];
    const t = fakeTransport({
      subscribe: async function* () {
        for (const ev of events) yield ev;
        await new Promise<void>(() => {});
      },
    });
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1)); // only the hydrated msg
    expect(result.current.log.entries.every((e) => e.kind !== "message" || e.message.role === "user")).toBe(
      true,
    );
  });

  it("folds stream events and re-reads the thread on a terminal event", async () => {
    const getThread = vi.fn().mockResolvedValue(THREAD);
    const t = fakeTransport({
      getThread,
      subscribe: async function* () {
        yield { type: "message_delta", text: "hello" } as AgentEvent;
        yield { type: "done" } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });
    render(t);
    // Once for hydration, again for the terminal re-read.
    await waitFor(() => expect(getThread.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  // An empty thread must leave an empty log rather than throwing — a brand-new
  // chat has nothing persisted yet.
  it("tolerates a transport with no persisted thread", async () => {
    const { result } = render(fakeTransport({ getThread: vi.fn().mockResolvedValue(null) }));
    await waitFor(() => expect(result.current.log.entries).toHaveLength(0));
    expect(result.current.log.streaming).toBe(false);
  });

  it("send enqueues through the transport and locks the composer", async () => {
    const t = fakeTransport();
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));
    await act(async () => {
      await result.current.send("  question  ");
    });
    expect(t.post).toHaveBeenCalledWith("question", undefined);
    expect(result.current.log.streaming).toBe(true);
  });

  it("send ignores a blank message", async () => {
    const t = fakeTransport();
    const { result } = render(t);
    await act(async () => {
      await result.current.send("   ");
    });
    expect(t.post).not.toHaveBeenCalled();
  });

  // An aborted POST is the user navigating away, not a turn failure.
  it("send swallows an abort without flagging an error", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    const { result } = render(fakeTransport({ post: vi.fn().mockRejectedValue(abort) }));
    await act(async () => {
      await result.current.send("q");
    });
    expect(result.current.log.error).toBeNull();
  });

  it("cancel tells the transport and unlocks the composer immediately", async () => {
    const t = fakeTransport();
    const { result } = render(t);
    await act(async () => {
      await result.current.send("q");
    });
    act(() => result.current.cancel());
    expect(t.requestCancel).toHaveBeenCalled();
    expect(result.current.log.streaming).toBe(false);
  });

  it("undo drops turns then re-reads, and is a no-op for a non-positive count", async () => {
    const t = fakeTransport();
    const { result } = render(t);
    await act(async () => {
      await result.current.undo(0);
    });
    expect(t.undoTurns).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.undo(2);
    });
    expect(t.undoTurns).toHaveBeenCalledWith(2);
  });

  it("mention notifies and adds an optimistic entry, and is a no-op with no users", async () => {
    const t = fakeTransport();
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));
    await act(async () => {
      await result.current.mention([], "nobody");
    });
    expect(t.addMention).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.mention(["bob"], "look");
    });
    expect(t.addMention).toHaveBeenCalledWith(["bob"], "look");
    expect(result.current.log.entries.some((e) => e.kind === "mention")).toBe(true);
  });
});
