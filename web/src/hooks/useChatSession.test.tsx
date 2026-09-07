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
  // The bug: your own message drawn twice. It was delivered twice — each pod
  // numbers its own broadcast, so a reconnect that lands elsewhere resumes
  // `?since=` in a numbering that was never its own and replays what the
  // viewer already has. Nothing downstream could tell: `user_message` folds
  // with an unconditional push, and the persisted-thread reconcile treats a
  // longer local log as "the store is behind" and keeps it. So the duplicate
  // stuck until the chat was reopened.
  it("draws an event once even when it is delivered twice", async () => {
    const t = fakeTransport({
      subscribe: async function* () {
        yield { type: "user_message", author: "u", content: "hi", created_at: 1, id: "e1" } as AgentEvent;
        yield { type: "user_message", author: "u", content: "hi", created_at: 1, id: "e1" } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });

    const { result } = render(t);

    await waitFor(() => expect(result.current.log.entries.length).toBeGreaterThan(0));
    expect(result.current.log.entries).toHaveLength(1);
  });

  it("keeps two genuinely different events that happen to read the same", async () => {
    // Saying the same thing twice is a thing people do, so the identity has to
    // come from the id and never from the content.
    const t = fakeTransport({
      subscribe: async function* () {
        yield { type: "user_message", author: "u", content: "hi", created_at: 1, id: "e1" } as AgentEvent;
        yield { type: "user_message", author: "u", content: "hi", created_at: 1, id: "e2" } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });

    const { result } = render(t);

    await waitFor(() => expect(result.current.log.entries).toHaveLength(2));
  });

  it("still folds events from a backend that sends no id", async () => {
    // A rolling upgrade serves both versions at once; dropping un-tagged events
    // would blank the stream for the length of the rollout.
    const t = fakeTransport({
      subscribe: async function* () {
        yield { type: "user_message", author: "u", content: "hi", created_at: 1 } as AgentEvent;
        yield { type: "user_message", author: "u", content: "yo", created_at: 2 } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });

    const { result } = render(t);

    await waitFor(() => expect(result.current.log.entries).toHaveLength(2));
  });

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

  // #714: the backend refuses the send outright when it cannot establish who is
  // asking. "messages failed: 500" is visible but tells nobody what happened.
  it("send explains an identity failure instead of echoing the status", async () => {
    const failed = Object.assign(new Error("messages failed: 500"), {
      status: 500,
      code: "request_env_failed",
    });
    const { result } = render(fakeTransport({ post: vi.fn().mockRejectedValue(failed) }));
    await waitFor(() => expect(result.current.log.entries.length).toBeGreaterThan(0)); // hydrated
    await act(async () => {
      await result.current.send("q");
    });
    expect(result.current.log.streaming).toBe(false);
    expect(result.current.log.error).toContain("沒有送出");
    expect(result.current.log.error).not.toContain("500");
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

  // #739: the context gauge hydrates once on mount, so without a refetch at the
  // end of a turn it would keep showing what the window held BEFORE the turn
  // ran — a bar that never moves is worse than no bar, because it is believed.
  it("refreshes the context gauge when a turn ends", async () => {
    const { QueryClientProvider } = await import("@tanstack/react-query");
    const { makeTestQueryClient } = await import("../test/queryWrapper");
    const qc = makeTestQueryClient();
    qc.setQueryData(["ctx", "c1"], { used: 1, limit: 100, measured: true });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const t = fakeTransport({
      contextKey: ["ctx", "c1"],
      subscribe: async function* () {
        yield { type: "done" } as AgentEvent;
        await new Promise<void>(() => {});
      },
    });
    renderHook(() => useChatSession(t, 60_000), { wrapper });
    await waitFor(() =>
      expect(qc.getQueryState(["ctx", "c1"])?.isInvalidated).toBe(true),
    );
  });
});

describe("the sender's own message", () => {
  // The bubble used to be drawn only by the `user_message` broadcast, which
  // `chat_send` publishes after the entire turn preamble — compaction, a cold
  // sandbox wake, context and skill file reads, the `/tokenize` probe. So the
  // one person who knows when they pressed send watched their words vanish for
  // as long as all that took.
  //
  // `post` here NEVER resolves: the failure at its extreme, and the reason this
  // cannot be tested by letting the fake transport return quickly. Nothing the
  // backend does may stand between typing and seeing.
  it("appears without waiting for the backend", async () => {
    const t = fakeTransport({ post: vi.fn(() => new Promise<void>(() => {})) });
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));

    act(() => {
      void result.current.send("hello");
    });

    await waitFor(() => expect(result.current.log.entries).toHaveLength(2));
    const drawn = result.current.log.entries.at(-1);
    expect(drawn?.kind === "message" && drawn.message.content).toBe("hello");
    // Under the sender's own id, not a placeholder: the broadcast adopts this
    // entry by author + content, so an id that disagrees with the one the
    // backend stamps means the broadcast draws a second bubble instead.
    expect(drawn?.kind === "message" && drawn.message.author).toBe("tester");
    // And the composer is locked, which the broadcast used to be what did.
    expect(result.current.log.streaming).toBe(true);
  });
});

describe("stopping", () => {
  // Stop used to flip `streaming` to false on the spot. That was a claim the
  // backend had not made: teardown lags, and for as long as it did the composer
  // said the turn had ended while it was still running — and, worse, unlocked
  // itself, so the next message queued behind a turn nobody had actually
  // stopped. `stopping` is the state that was missing: the request is out, the
  // turn has not ended, and neither button should pretend otherwise.
  it("stays streaming until the turn actually ends", async () => {
    const t = fakeTransport();
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));

    act(() => {
      void result.current.send("hello");
    });
    await waitFor(() => expect(result.current.log.streaming).toBe(true));

    act(() => result.current.cancel());

    expect(result.current.log.stopping).toBe(true);
    // The turn has NOT ended — only the request to end it has been sent.
    expect(result.current.log.streaming).toBe(true);
  });

  it("clears when the turn's terminal event arrives", async () => {
    let push: ((ev: AgentEvent) => void) | null = null;
    const t = fakeTransport({
      subscribe: async function* () {
        const queue: AgentEvent[] = [];
        let wake: (() => void) | null = null;
        push = (ev) => {
          queue.push(ev);
          wake?.();
        };
        while (true) {
          if (queue.length) {
            yield queue.shift() as AgentEvent;
            continue;
          }
          await new Promise<void>((r) => (wake = r));
        }
      },
    });
    const { result } = render(t);
    await waitFor(() => expect(push).not.toBeNull());

    act(() => {
      void result.current.send("hello");
    });
    act(() => result.current.cancel());
    await waitFor(() => expect(result.current.log.stopping).toBe(true));

    act(() => push?.({ type: "run_cancelled" } as AgentEvent));

    await waitFor(() => expect(result.current.log.stopping).toBe(false));
    expect(result.current.log.streaming).toBe(false);
  });
});

describe("a send that was refused leaves nothing behind", () => {
  // `drawOwnAsk` puts the words on screen before the POST resolves, which is the
  // point. When the POST then FAILS the message was never persisted, so no
  // broadcast will ever adopt that entry and the store poll cannot remove it
  // (`reconcileSnapshot` bails when the snapshot is shorter than the screen —
  // exactly this case). It sat there, contradicted by the error beside it.
  //
  // Worse than cosmetic: an entry with no `at` counts as its own turn to
  // `turnsFromEntry`, so "undo to here" asked the backend for one turn MORE than
  // the user pointed at — and undo deletes irreversibly.
  const refuse = (status: number) => {
    const err = Object.assign(new Error("nope"), { status });
    return vi.fn(() => Promise.reject(err));
  };

  it("takes the message back when the send is refused", async () => {
    const t = fakeTransport({ post: refuse(507) });
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));

    await act(async () => {
      await result.current.send("please do the thing");
    });

    expect(result.current.log.entries).toHaveLength(1);
    expect(result.current.log.error).not.toBeNull();
  });

  it("keeps it on a gateway cut, which is not a refusal", async () => {
    // 502/504 mean the request was cut, not that the turn failed — the message
    // may well be running server-side. Taking it back there would hide a
    // message that IS in the thread.
    const t = fakeTransport({ post: refuse(504) });
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));

    await act(async () => {
      await result.current.send("please do the thing");
    });

    expect(result.current.log.entries).toHaveLength(2);
  });

  it("keeps the message when the failure came AFTER the backend stored it", async () => {
    // The criterion is "did the backend persist it", not "was it a gateway cut".
    // `chat_send` writes the user's message and only THEN prepares the turn, so
    // anything that fails in the preparation — a sandbox that will not wake, a
    // context build that throws — answers 500 with the message already in the
    // thread. Retracting there takes it off the sender's screen while the agent
    // still reads it next turn, so they retype it and the thread has two.
    const err = Object.assign(new Error("boom"), { status: 500 });
    const t = fakeTransport({ post: vi.fn(() => Promise.reject(err)) });
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));

    await act(async () => {
      await result.current.send("please do the thing");
    });

    expect(result.current.log.entries).toHaveLength(2);
    expect(result.current.log.error).not.toBeNull();
  });

  it("a new send clears a Stop that is still pending", async () => {
    // `retryTurn` is `cancel()` then `send()`. Without this the retry inherits
    // `stopping`, so the turn it just started cannot be stopped and nothing can
    // be sent — and the terminal event that would clear it belongs to the turn
    // being abandoned, which is the reason retry exists.
    const t = fakeTransport();
    const { result } = render(t);
    await waitFor(() => expect(result.current.log.entries).toHaveLength(1));

    act(() => {
      void result.current.send("first");
    });
    act(() => result.current.cancel());
    expect(result.current.log.stopping).toBe(true);

    act(() => {
      void result.current.send("again");
    });

    expect(result.current.log.stopping).toBe(false);
  });
});
