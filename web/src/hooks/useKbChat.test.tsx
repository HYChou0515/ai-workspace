// @vitest-environment happy-dom
import { act, renderHook as rtlRenderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../api/kbMock";
import type { AgentEntry } from "../pages/investigation/agentLog";
import { QueryWrap } from "../test/queryWrapper";
import { useKbChat } from "./useKbChat";

// useKbChat hydrates through TanStack Query — give every hook a client.
const renderHook = <T,>(cb: () => T) =>
  rtlRenderHook(cb, { wrapper: QueryWrap });

const assistantText = (entries: AgentEntry[]): string =>
  entries
    .filter((e) => e.kind === "message" && e.message.role === "assistant")
    .map((e) => (e.kind === "message" ? e.message.content : ""))
    .join("");

describe("useKbChat", () => {
  beforeEach(() => _resetKbMock());

  it("creates a thread on first send, streams, then loads the cited answer", async () => {
    const onChatCreated = vi.fn();
    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], client: mockKbApi, onChatCreated }),
    );
    expect(result.current.chatId).toBeNull();

    await act(async () => {
      await result.current.send("why voids?");
    });

    expect(result.current.chatId).not.toBeNull();
    expect(onChatCreated).toHaveBeenCalledWith(result.current.chatId);

    // Waited for, not asserted straight after the send: the turn ends on the
    // SUBSCRIPTION now, and the citation-carrying snapshot is fetched by that
    // terminal event rather than by the send returning. The send resolving no
    // longer means the answer is in — that is the whole point of it being able
    // to queue.
    await waitFor(() => expect(result.current.log.streaming).toBe(false));
    await waitFor(() => {
      const answer = result.current.log.entries.find(
        (e) => e.kind === "message" && e.message.role === "assistant",
      );
      expect(answer?.kind === "message" && answer.message.citations?.[0]?.filename).toBe(
        "reflow.md",
      );
    });
    // the snapshot has a user message, a kb_search tool call, and the answer
    expect(result.current.log.entries.map((e) => e.kind)).toContain("tool_call");
    expect(assistantText(result.current.log.entries)).toContain("[1]");
  });

  it("forwards an attached image to sendMessage (#513 P10)", async () => {
    const spy = vi.spyOn(mockKbApi, "sendMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    const image = { data: "AQID", mime: "image/png" };

    await act(async () => {
      await result.current.send("what is this?", image);
    });

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ content: "what is this?", image }));
    spy.mockRestore();
  });

  it("sends an image-only message (no text) (#513 P10)", async () => {
    const spy = vi.spyOn(mockKbApi, "sendMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    const image = { data: "AQID", mime: "image/png" };

    await act(async () => {
      await result.current.send("   ", image); // whitespace text but a real image
    });

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ image }));
    spy.mockRestore();
  });

  it("ignores empty input", async () => {
    const { result } = renderHook(() => useKbChat({ collectionIds: [], client: mockKbApi }));
    await act(async () => {
      await result.current.send("   ");
    });
    expect(result.current.chatId).toBeNull();
    expect(result.current.log.entries).toEqual([]);
  });

  it("hydrates an existing thread's history", async () => {
    const chat = await mockKbApi.createChat("t", ["col-1"]);
    await mockKbApi.sendMessage({ chatId: chat.resource_id, content: "q" });

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], chatId: chat.resource_id, client: mockKbApi }),
    );
    await waitFor(() => expect(result.current.log.entries.length).toBeGreaterThan(0));
    expect(result.current.log.entries[0].kind).toBe("message");
  });

  it("reset clears the thread back to a fresh one", async () => {
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    await act(async () => {
      await result.current.send("hello");
    });
    act(() => result.current.reset());
    expect(result.current.chatId).toBeNull();
    expect(result.current.log.entries).toEqual([]);
  });

  it("cancel tells the BE to tear down the in-flight turn", async () => {
    const spy = vi.spyOn(mockKbApi, "cancelMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: ["col-1"], client: mockKbApi }));
    await act(async () => {
      await result.current.send("hello"); // creates the thread → a turn to cancel
    });
    act(() => result.current.cancel());
    expect(spy).toHaveBeenCalledWith(result.current.chatId);
    spy.mockRestore();
  });

  it("cancel with no active thread only aborts locally (no BE call)", () => {
    const spy = vi.spyOn(mockKbApi, "cancelMessage");
    const { result } = renderHook(() => useKbChat({ collectionIds: [], client: mockKbApi }));
    act(() => result.current.cancel());
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("useKbChat — the subscription outlives a dropped connection", () => {
  beforeEach(() => _resetKbMock());

  // A stream that ENDS is not a stream that is finished. An idle proxy cutting
  // the connection, a pod rollover and `close_streams` all end the generator
  // without throwing, so a loop that only caught errors stopped for good — and
  // because the ref still held the chat id, nothing re-attached. The chat went
  // permanently deaf: sends returned 202, `streaming` stayed true, and no answer
  // ever rendered again until the component remounted.
  it("re-attaches after the stream ends cleanly, and folds what arrives next", async () => {
    let connects = 0;
    const client = {
      ...mockKbApi,
      getChat: vi.fn().mockResolvedValue({
        resource_id: "kb-live",
        title: "",
        collection_ids: [],
        owner: "default-user",
        shared_with: [],
        messages: [],
      }),
      subscribeChat: async function* () {
        connects += 1;
        if (connects === 1) return; // a clean EOF: no error, just gone
        yield { type: "message_delta", text: "回來了" } as never;
        await new Promise<void>(() => {}); // then stay open
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], chatId: "kb-live", client }),
    );

    // The first backoff is 1s, so give it room — and assert on the CONTENT, not
    // on the connect count: reconnecting and then dropping what arrives would
    // satisfy a counter and still leave the chat blank.
    await waitFor(
      () =>
        expect(
          result.current.log.entries.some(
            (e) => e.kind === "message" && e.message.content.includes("回來了"),
          ),
        ).toBe(true),
      { timeout: 4000 },
    );
    expect(connects).toBeGreaterThan(1);
  });
});

describe("useKbChat — a turn that ended while we were away", () => {
  beforeEach(() => _resetKbMock());

  // Reconnecting is not enough. The replay ring lives on the POD's session, so
  // a reconnect that lands elsewhere — the rollover this loop exists for —
  // resumes into an empty ring and the terminal event is gone for good. Without
  // a re-hydrate, `streaming` stays true forever with the finished answer
  // sitting unread in the store: the same symptom as never reconnecting, moved
  // one step later. There is no store-poll on this surface to catch it.
  it("re-reads the thread after a drop and stops claiming to be streaming", async () => {
    const ended = {
      resource_id: "kb-gone",
      title: "",
      collection_ids: [],
      owner: "default-user",
      shared_with: [],
      messages: [
        { role: "user", content: "問題", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: 1, citations: [] },
        { role: "assistant", content: "答完了", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: 2, citations: [] },
      ],
    };
    let connects = 0;
    // The thread is EMPTY when the view first hydrates and carries the finished
    // answer only afterwards — which is the real sequence, and the only one in
    // which "streaming stopped" says anything: seeded with the ended thread from
    // the start, mount alone would satisfy the assertion.
    const empty = { ...ended, messages: [] };
    let reads = 0;
    const client = {
      ...mockKbApi,
      getChat: vi.fn(async () => (reads++ === 0 ? empty : ended)),
      subscribeChat: async function* () {
        if (connects++ === 0) {
          // A turn IS in flight when the connection dies — that is what makes
          // the assertion below mean anything. Asserting "not streaming" on a
          // log that was never streaming is true before the hook does a thing.
          yield { type: "user_message", author: "default-user", content: "問題", created_at: 1 } as never;
          // Held open a moment so "a turn is in flight" is observable before the
          // drop — otherwise this test races its own premise.
          await new Promise<void>((r) => setTimeout(r, 300));
          return; // …and then the stream is simply gone
        }
        // The pod it reconnects to knows nothing about the turn that finished:
        // an empty replay ring, and no terminal event is ever coming.
        await new Promise<void>(() => {});
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], chatId: "kb-gone", client }),
    );

    await waitFor(() => expect(result.current.log.streaming).toBe(true));
    await waitFor(() => expect(result.current.log.streaming).toBe(false), { timeout: 5000 });
    expect(
      result.current.log.entries.some(
        (e) => e.kind === "message" && e.message.content.includes("答完了"),
      ),
    ).toBe(true);
  });
});

describe("useKbChat — a turn that FAILS still says why", () => {
  beforeEach(() => _resetKbMock());

  const failedThread = {
    resource_id: "kb-boom",
    title: "",
    collection_ids: [],
    owner: "default-user",
    shared_with: [],
    messages: [
      { role: "user", content: "問題", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: 1, citations: [] },
      { role: "error", content: "model exploded", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: 2, citations: [] },
    ],
  };

  // The runner gives up by yielding RunError and then RunDone — always both,
  // always in that order. So anything that clears `error` on each event wipes
  // the failure one event after it arrives, and the chat just stops with no
  // explanation. `agentLog`'s `message_delta` comment says this exact defect
  // must not come back; it came back through the reconnect loop instead.
  it("keeps the turn's error when `done` follows it", async () => {
    const client = {
      ...mockKbApi,
      getChat: vi.fn().mockResolvedValue(failedThread),
      subscribeChat: async function* () {
        yield { type: "error", message: "model exploded" } as never;
        yield { type: "done" } as never;
        await new Promise<void>(() => {});
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], chatId: "kb-boom", client }),
    );

    await waitFor(() => expect(result.current.log.error).toContain("model exploded"));
    // …and it is still there once everything has settled, not just in the frame
    // between the two events.
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 50));
    });
    expect(result.current.log.error).toContain("model exploded");
  });

  // The post-send safety net gates on "the turn ended". A failed, cancelled or
  // step-limited turn persists `role="error"` (`turns._error_message`), so a gate
  // reading `role === "assistant"` skipped exactly the turns most likely to have
  // lost their broadcast — leaving the composer locked and the spinner spinning.
  it("unsticks a new chat whose first turn ended in an error", async () => {
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-boom" }),
      getChat: vi.fn().mockResolvedValue(failedThread),
      sendMessage: vi.fn().mockResolvedValue(undefined),
      subscribeChat: async function* () {
        await new Promise<void>(() => {}); // the broadcast is missed entirely
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() => useKbChat({ collectionIds: ["c1"], client }));
    await act(async () => {
      await result.current.send("問題");
    });

    await waitFor(() => expect(result.current.log.streaming).toBe(false));
  });
});

describe("useKbChat — a thread whose tail is a #624 notice is still MID-turn", () => {
  beforeEach(() => _resetKbMock());

  // `_note_kb_reduction` appends `role="notice"` BEFORE the turn is enqueued, so
  // it is the thread's tail for the whole time-to-first-token window. Treating
  // "not a user message" as "the turn ended" therefore reconciled mid-flight on
  // every long thread that had just crossed the context horizon — and because a
  // notice counts as content, the snapshot beat the screen and deleted the
  // answer that had already streamed into it.
  it("does not reconcile a mid-turn thread away when the send detaches", async () => {
    const midTurn = {
      resource_id: "kb-notice",
      title: "",
      collection_ids: [],
      owner: "default-user",
      shared_with: [],
      messages: [
        { role: "user", content: "問題", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: 1, citations: [] },
        { role: "notice", content: "較早的對話已不在模型視窗內", reasoning: null, tool_name: null, tool_args: null, tool_call_id: null, created_at: 2, citations: [] },
      ],
    };
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-notice" }),
      getChat: vi.fn().mockResolvedValue(midTurn),
      sendMessage: vi.fn().mockResolvedValue(undefined),
      subscribeChat: async function* () {
        // The answer is already arriving when the send's 25s deadline detaches.
        yield { type: "message_delta", text: "已經開始回答了" } as never;
        await new Promise<void>(() => {});
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() => useKbChat({ collectionIds: ["c1"], client }));
    await act(async () => {
      await result.current.send("問題");
    });
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 60));
    });

    // The answer on screen survives, and the turn is still running.
    expect(
      result.current.log.entries.some(
        (e) => e.kind === "message" && e.message.content.includes("已經開始回答了"),
      ),
    ).toBe(true);
    expect(result.current.log.streaming).toBe(true);
  });
});

describe("useKbChat — reconnecting must not eat what is on screen", () => {
  beforeEach(() => _resetKbMock());

  const thread = (messages: unknown[]) => ({
    resource_id: "kb-r",
    title: "",
    collection_ids: [],
    owner: "default-user",
    shared_with: [],
    messages,
  });
  const msg = (role: string, content: string, created_at: number) => ({
    role, content, reasoning: null, tool_name: null, tool_args: null,
    tool_call_id: null, created_at, citations: [],
  });

  // The gate went onto the SEND path's re-hydrate and not the reconnect's, so a
  // mid-turn thread — tail `notice`, the #624 marker written before the turn is
  // even enqueued — was re-hydrated by the loop anyway.
  //
  // What that costs was MEASURED rather than reasoned about, and it is not what
  // the reasoning said: `reconcileSnapshot` merges rather than replaces, so the
  // streamed answer survives. The snapshot's copy of the question arrives beside
  // the broadcast's instead — `["user:問題", "notice", "user:問題", "assistant:…"]`
  // — which is the duplicate bubble the whole adoption machinery exists to stop.
  // Asserting the property that was actually observed, not the mechanism that
  // was assumed.
  it("does not redraw the question when a mid-turn thread is re-read (a notice tail)", async () => {
    // The thread is EMPTY when the view hydrates, so the two content entries the
    // stream then puts on screen TIE with the snapshot's two. That tie is the
    // whole scenario: `reconcileSnapshot` bails only when the screen is strictly
    // ahead, so on a tie the snapshot wins and takes the answer with it. Seeded
    // the other way the screen is ahead, the bail protects it anyway, and the
    // test proves nothing — which is how the first version of this passed with
    // both gates removed.
    // Keyed on the SCENARIO, not on a call count: hydration may read more than
    // once, and a fixture that switches on "the second read" then feeds the
    // mid-turn thread to hydration instead of to the reconnect — a different
    // test wearing this one's name, which is how the first version of this
    // failed against its own fix.
    let dropped = false;
    let connects = 0;
    const client = {
      ...mockKbApi,
      getChat: vi.fn(async () =>
        dropped
          ? thread([msg("user", "問題", 1), msg("notice", "較早的對話已不在視窗內", 2)])
          : thread([]),
      ),
      subscribeChat: async function* () {
        // The RECONNECT yields nothing — that is the case this whole loop is
        // for: a pod whose replay ring knows nothing about the turn. A double
        // that re-sent its events on every connect duplicated the question by
        // itself (these carry no event id, so the de-dupe cannot see them), and
        // the test then measured the double instead of the hook.
        if (connects++ > 0) {
          await new Promise<void>(() => {});
          return;
        }
        // After the initial hydration, which would otherwise replace the log
        // (and the delta with it) a beat later.
        await new Promise<void>((r) => setTimeout(r, 60));
        // The question's broadcast comes first, as the backend sends it — and it
        // is what puts the log into "a turn is in flight". Without it nothing in
        // this scenario ever sets `streaming`, and the assertion below would be
        // measuring the fixture rather than the code.
        yield {
          type: "user_message", author: "default-user", content: "問題", created_at: 1,
        } as never;
        yield { type: "message_delta", text: "答案開頭" } as never;
        dropped = true;
        throw new Error("kb stream failed: 502"); // …and the connection dies
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], chatId: "kb-r", client }),
    );
    await waitFor(() =>
      expect(
        result.current.log.entries.some(
          (e) => e.kind === "message" && e.message.content.includes("答案開頭"),
        ),
      ).toBe(true),
    );
    // Past the 1s backoff, where the re-hydrate lands.
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 1400));
    });

    expect(
      result.current.log.entries.filter(
        (e) => e.kind === "message" && e.message.content === "問題",
      ),
    ).toHaveLength(1);
    // …and the answer is still there, which is the other half of "nothing was
    // disturbed".
    expect(
      result.current.log.entries.some(
        (e) => e.kind === "message" && e.message.content.includes("答案開頭"),
      ),
    ).toBe(true);
    expect(result.current.log.streaming).toBe(true);
  });

  // "Is the screen ahead of the store" is not answerable from the store. While a
  // send is out its tail is still the PREVIOUS turn's answer, which reads as
  // finished — so re-hydrating there erased the question the user had just typed
  // and took Stop away from a turn being accepted.
  it("leaves a just-sent question alone when the drop happens during the send", async () => {
    let releaseSend!: () => void;
    const client = {
      ...mockKbApi,
      getChat: vi.fn().mockResolvedValue(
        thread([msg("user", "舊問題", 1), msg("assistant", "舊答案", 2)]),
      ),
      sendMessage: vi.fn(() => new Promise<void>((r) => (releaseSend = r))),
      subscribeChat: async function* () {
        throw new Error("kb stream failed: 502"); // drops at once, retries at 1s
      },
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], chatId: "kb-r", client }),
    );
    // Let the initial hydration land first; otherwise it replaces the log after
    // the question is drawn and this measures the wrong thing.
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 80));
    });
    let sending!: Promise<void>;
    await act(async () => {
      sending = result.current.send("新問題");
      await Promise.resolve();
    });
    // The reconnect's re-hydrate would land here, while the POST is still out.
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 1400));
    });

    expect(
      result.current.log.entries.some(
        (e) => e.kind === "message" && e.message.content === "新問題",
      ),
    ).toBe(true);
    expect(result.current.log.streaming).toBe(true);

    releaseSend();
    await act(async () => {
      await sending;
    });
  });
});

describe("useKbChat — send failure", () => {
  beforeEach(() => _resetKbMock());

  // A failing stream must land in the log as a turn error. Swallowing it leaves
  // the composer unlocked with no explanation, which is indistinguishable from
  // "the model had nothing to say".
  it("surfaces a refused send as a turn error and unlocks the composer", async () => {
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-1" }),
      sendMessage: vi.fn().mockRejectedValue(new Error("kb message failed: 503")),
      subscribeChat: async function* () {},
    } as unknown as typeof mockKbApi;

    const { result } = renderHook(() => useKbChat({ collectionIds: ["c1"], client }));
    await act(async () => {
      await result.current.send("q");
    });

    expect(result.current.log.error).toContain("503");
    expect(result.current.log.streaming).toBe(false);
    // …and the question is taken back off the screen. Nothing was stored, so no
    // broadcast will ever adopt it — left drawn it would sit there with the
    // error beside it saying it was never sent.
    expect(
      result.current.log.entries.filter((e) => e.kind === "message" && e.message.role === "user"),
    ).toEqual([]);
  });

  // Navigating away aborts the subscription. That is not a failure, and saying
  // it was would put an error on a chat the reader has already left — which
  // they then find waiting for them when they come back.
  it("treats an aborted subscription as a departure, not an error", async () => {
    let signalled: AbortSignal | undefined;
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-2" }),
      sendMessage: vi.fn().mockResolvedValue(undefined),
      subscribeChat: async function* (_id: string, signal?: AbortSignal) {
        signalled = signal;
        // Hangs until aborted, exactly as a live stream does.
        await new Promise<void>((resolve) =>
          signal?.addEventListener("abort", () => resolve(), { once: true }),
        );
        throw Object.assign(new Error("aborted"), { name: "AbortError" });
      },
    } as unknown as typeof mockKbApi;

    const { result, unmount } = renderHook(() => useKbChat({ collectionIds: ["c1"], client }));
    await act(async () => {
      await result.current.send("q");
    });
    unmount();
    await act(async () => {
      await Promise.resolve();
    });

    expect(signalled?.aborted).toBe(true);
    expect(result.current.log.error).toBeNull();
  });

  // A stream that ends after the view is gone must write nothing. In a real
  // browser a set-state-after-unmount is merely pointless; in a torn-down test
  // environment it throws `ReferenceError: window is not defined` out of React
  // and reddens whichever FILE happened to be running, somewhere else entirely.
  // Deleting `globalThis.window` is that environment, reproduced on purpose.
  it("writes nothing when the send settles after unmount", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const client = {
      ...mockKbApi,
      createChat: vi.fn().mockResolvedValue({ resource_id: "kb-3" }),
      subscribeChat: async function* () {},
      sendMessage: vi.fn(async () => {
        await gate;
        // The shape that actually happens: the request dies as the view goes.
        throw new Error("the send died after the view was gone");
      }),
    } as unknown as typeof mockKbApi;

    const { result, unmount } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], client }),
    );
    let sent!: Promise<void>;
    await act(async () => {
      sent = result.current.send("q");
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.log.streaming).toBe(true));

    unmount();
    const realWindow = globalThis.window;
    // @ts-expect-error — reproducing the torn-down environment
    delete globalThis.window;
    try {
      release();
      await expect(sent).resolves.toBeUndefined();
    } finally {
      globalThis.window = realWindow;
    }
  });

  // The same rule one await earlier: the thread is created, and by the time the
  // id comes back there is nobody to hand it to. What must NOT happen is
  // dropping the question — the thread already exists on the server, so a
  // skipped send leaves an empty chat and the typed question nowhere.
  it("writes nothing when the thread is created after unmount, but still asks", async () => {
    let release!: (v: { resource_id: string }) => void;
    const sendMessage = vi.fn(mockKbApi.sendMessage.bind(mockKbApi));
    const client = {
      ...mockKbApi,
      sendMessage,
      createChat: vi.fn(
        () => new Promise<{ resource_id: string }>((r) => (release = r)),
      ),
    } as unknown as typeof mockKbApi;

    const { result, unmount } = renderHook(() =>
      useKbChat({ collectionIds: ["c1"], client }),
    );
    let sent!: Promise<void>;
    await act(async () => {
      sent = result.current.send("q");
      await Promise.resolve();
    });

    unmount();
    const realWindow = globalThis.window;
    // @ts-expect-error — reproducing the torn-down environment
    delete globalThis.window;
    try {
      release({ resource_id: "kb-4" });
      await expect(sent).resolves.toBeUndefined();
    } finally {
      globalThis.window = realWindow;
    }
    expect(sendMessage).toHaveBeenCalledWith(
      expect.objectContaining({ chatId: "kb-4", content: "q" }),
    );
  });
});
