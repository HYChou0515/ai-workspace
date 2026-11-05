// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { Message } from "../api/types";
import { makeTestQueryClient } from "../test/queryWrapper";
import { type ChatThread, useChatLog } from "./useChatLog";

const msg = (role: "user" | "assistant", content: string): Message =>
  ({ role, content, created_at: "2026-11-05T00:00:00Z" }) as unknown as Message;

const KEY = ["itemChat", "rca", "i1", "c1"] as const;

function texts(entries: readonly unknown[]): string {
  return JSON.stringify(entries);
}

describe("useChatLog", () => {
  it("shows the thread as the server has it now, not the copy cached by an earlier visit", async () => {
    // Found while recording a demo (and reproduced on an ordinary item): send
    // a message, leave by an in-app link, come back — the chat was EMPTY. The
    // hook seeded once per thread from the cache an earlier mount left (taken
    // before the message), and ignored the refetch that brought it.
    const qc = makeTestQueryClient();
    qc.setQueryData<ChatThread>(KEY, { messages: [] });
    const getThread = vi.fn(async (): Promise<ChatThread> => ({ messages: [msg("user", "這週有哪些訂單延遲出貨？")] }));
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useChatLog({ threadKey: "c1", queryKey: [...KEY], getThread }), { wrapper });

    await waitFor(() => expect(texts(result.current.log.entries)).toContain("這週有哪些訂單延遲出貨？"));
    expect(getThread).toHaveBeenCalled();
  });

  it("still opens on what the cache holds while the fresh copy is on its way", async () => {
    const qc = makeTestQueryClient();
    qc.setQueryData<ChatThread>(KEY, { messages: [msg("user", "earlier question")] });
    const getThread = vi.fn(() => new Promise<ChatThread>(() => {}));
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useChatLog({ threadKey: "c1", queryKey: [...KEY], getThread }), { wrapper });

    await waitFor(() => expect(texts(result.current.log.entries)).toContain("earlier question"));
  });
});
