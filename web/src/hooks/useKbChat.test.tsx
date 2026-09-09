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
