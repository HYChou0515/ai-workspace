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

  // #613: `todos_updated` is panel state, not transcript — it must land in the
  // todos query cache (whole-list replace) and never fold into the log.
  it("writes todos_updated into the todos cache and keeps it out of the log", async () => {
    const { QueryClientProvider } = await import("@tanstack/react-query");
    const { makeTestQueryClient } = await import("../test/queryWrapper");
    const qc = makeTestQueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const items = [{ text: "fix bug", status: "in_progress" }];
    const t = fakeTransport({
      todosKey: ["todos", "c1"],
      subscribe: async function* () {
        yield { type: "todos_updated", items } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });
    const { result } = renderHook(() => useChatSession(t, 60_000), { wrapper });
    await waitFor(() => expect(qc.getQueryData(["todos", "c1"])).toEqual(items));
    // Only the hydrated user message — the todo event added no entry.
    expect(result.current.log.entries).toHaveLength(1);
  });

  // #613 P3: `goal_updated` merges into the goal cache (keeping the cached
  // deploy-level checker flag) and never folds into the log; a terminal goal
  // state also refetches the thread so the persisted marker appears.
  it("merges goal_updated into the goal cache and refetches on a terminal state", async () => {
    const { QueryClientProvider } = await import("@tanstack/react-query");
    const { makeTestQueryClient } = await import("../test/queryWrapper");
    const qc = makeTestQueryClient();
    qc.setQueryData(["goal", "c1"], { goal: null, checker_enabled: false });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const met = {
      condition: "done",
      set_by: "me",
      rounds_used: 1,
      state: "met",
      max_rounds: 3,
    };
    const MARKED = {
      messages: [
        { role: "user" as const, content: "q" },
        { role: "goal" as const, content: "目標已達成:done" },
      ],
    };
    const getThread = vi.fn().mockResolvedValueOnce(THREAD).mockResolvedValue(MARKED);
    const t = fakeTransport({
      getThread,
      goalKey: ["goal", "c1"],
      subscribe: async function* () {
        yield { type: "goal_updated", goal: met } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });
    const { result } = renderHook(() => useChatSession(t, 60_000), { wrapper });
    await waitFor(() =>
      expect(qc.getQueryData(["goal", "c1"])).toEqual({ goal: met, checker_enabled: false }),
    );
    // met ⇒ thread re-read AND reconciled into the visible log, so the persisted
    // `role="goal"` marker appears live (the #613 live probe: it only showed
    // after a manual reload when this merely invalidated the cache).
    await waitFor(() => expect(getThread.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() =>
      expect(result.current.log.entries.some((e) => e.kind === "goal_note")).toBe(true),
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
