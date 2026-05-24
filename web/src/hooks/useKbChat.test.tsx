// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { mockKbApi, _resetKbMock } from "../api/kbMock";
import { useKbChat } from "./useKbChat";

describe("useKbChat", () => {
  beforeEach(() => _resetKbMock());

  it("creates a thread on first send, streams, then loads the cited answer", async () => {
    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], client: mockKbApi }),
    );
    expect(result.current.chatId).toBeNull();

    await act(async () => {
      await result.current.send("why voids?");
    });

    expect(result.current.chatId).not.toBeNull(); // thread created
    expect(result.current.streaming).toBe(false);
    const roles = result.current.messages.map((m) => m.role);
    expect(roles).toEqual(["user", "assistant"]);
    const answer = result.current.messages[1];
    expect(answer.content).toContain("[1]");
    expect(answer.citations[0].filename).toBe("reflow.md"); // refetched, cited
  });

  it("ignores empty input", async () => {
    const { result } = renderHook(() =>
      useKbChat({ collectionIds: [], client: mockKbApi }),
    );
    await act(async () => {
      await result.current.send("   ");
    });
    expect(result.current.chatId).toBeNull();
    expect(result.current.messages).toEqual([]);
  });

  it("hydrates an existing thread's history", async () => {
    const chat = await mockKbApi.createChat("t", ["col-1"]);
    const consume = async () => {
      for await (const _ of mockKbApi.streamMessage({ chatId: chat.resource_id, content: "q" }));
    };
    await consume();

    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], chatId: chat.resource_id, client: mockKbApi }),
    );
    await waitFor(() => expect(result.current.messages.length).toBe(2));
    expect(result.current.messages[0].role).toBe("user");
  });

  it("reset clears the thread back to a fresh one", async () => {
    const { result } = renderHook(() =>
      useKbChat({ collectionIds: ["col-1"], client: mockKbApi }),
    );
    await act(async () => {
      await result.current.send("hello");
    });
    act(() => result.current.reset());
    expect(result.current.chatId).toBeNull();
    expect(result.current.messages).toEqual([]);
  });
});
