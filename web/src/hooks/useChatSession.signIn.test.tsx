// @vitest-environment happy-dom
/**
 * `docs/plan-env-token-refresh.md` D6 puts a `sign_in` message under the
 * question BEFORE its turn runs, so a thread whose tail is that message is still
 * waiting for its reply (review round 1). The store poll — the only word a
 * cross-pod viewer gets that a turn ended — must not read it as the answer.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { type BroadcastChatTransport, useChatSession } from "./useChatSession";

vi.mock("../api", () => ({ api: { getCurrentUser: vi.fn().mockResolvedValue("tester") } }));

const question = { role: "user" as const, content: "q", author: "amy", created_at: Date.now() };
const signIn = {
  role: "sign_in" as const,
  content: "The sign-in to ERP has expired.",
  sign_in_provider: "erp",
  sign_in_layer: "personal" as const,
  created_at: Date.now(),
};

function transport(getThread: BroadcastChatTransport["getThread"]): BroadcastChatTransport {
  return {
    threadKey: "c1",
    queryKey: ["chat", "c1"],
    filesKey: ["files", "it"],
    getThread,
    subscribe: async function* () {
      await new Promise<void>(() => {}); // a pod that is not running the turn: silent
    },
    post: vi.fn().mockResolvedValue(undefined),
    requestCancel: vi.fn(),
    undoTurns: vi.fn().mockResolvedValue(undefined),
    addMention: vi.fn().mockResolvedValue(undefined),
  };
}

afterEach(() => vi.restoreAllMocks());

describe("useChatSession — a sign-in notice under the question", () => {
  it("hydrates as a turn still running", async () => {
    const t = transport(vi.fn().mockResolvedValue({ messages: [question, signIn] }));
    const { result } = renderHook(() => useChatSession(t, 60_000), { wrapper: QueryWrap });

    await waitFor(() => expect(result.current.log.entries.some((e) => e.kind === "sign_in")).toBe(true));
    expect(result.current.log.streaming).toBe(true);
    expect(result.current.log.streamingBy).toBe("amy");
  });

  it("is not the reply the store poll is waiting for", async () => {
    const getThread = vi
      .fn()
      .mockResolvedValueOnce({ messages: [question] })
      .mockResolvedValue({ messages: [question, signIn] });
    const t = transport(getThread);
    const { result } = renderHook(() => useChatSession(t, 20), { wrapper: QueryWrap });

    await waitFor(() => expect(getThread.mock.calls.length).toBeGreaterThan(2));
    expect(result.current.log.streaming).toBe(true);
  });
});
