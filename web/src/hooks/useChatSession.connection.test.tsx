// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent } from "../events";
import { QueryWrap } from "../test/queryWrapper";
import { type BroadcastChatTransport, useChatSession } from "./useChatSession";

vi.mock("../api", () => ({ api: { getCurrentUser: vi.fn().mockResolvedValue("tester") } }));

/**
 * Losing the live stream was completely invisible.
 *
 * The subscription's `catch` swallowed every non-abort error — no banner, no
 * state, not even a `console.error` — so an idle-proxy cut or a pod rollover
 * looked exactly like a chat where nothing was happening. Meanwhile the answer
 * on screen simply stopped growing, because live events are dropped when nobody
 * is attached and there is no replay.
 *
 * The content is no longer at risk (the turn is persisted and re-read), so what
 * is left is entirely a question of TELLING the user: a frozen answer with
 * "reconnecting…" on it is a wait; the same frozen answer in silence is a
 * hang they can only interpret as broken.
 */

const THREAD = { messages: [{ role: "user" as const, content: "q" }] };

function transport(over: Partial<BroadcastChatTransport> = {}): BroadcastChatTransport {
  return {
    threadKey: "c1",
    queryKey: ["chat", "c1"],
    filesKey: ["files", "it"],
    getThread: vi.fn().mockResolvedValue(THREAD),
    subscribe: async function* () {
      await new Promise<void>(() => {}); // healthy, quiet stream
    },
    post: vi.fn().mockResolvedValue(undefined),
    requestCancel: vi.fn(),
    undoTurns: vi.fn().mockResolvedValue(undefined),
    addMention: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
}

const render = (t: BroadcastChatTransport) =>
  renderHook(() => useChatSession(t, 60_000), { wrapper: QueryWrap });

afterEach(() => vi.restoreAllMocks());

describe("useChatSession — connection state", () => {
  it("reports a healthy subscription as live", async () => {
    const { result } = render(transport());
    await waitFor(() => expect(result.current.connection.state).toBe("live"));
  });

  it("reports reconnecting, with the reason, when the stream drops", async () => {
    const { result } = render(
      transport({
        // eslint-disable-next-line require-yield
        subscribe: async function* () {
          throw new Error("stream failed: 504");
        },
      }),
    );

    await waitFor(() => expect(result.current.connection.state).toBe("reconnecting"));
    // The reason was previously swallowed entirely — not even logged.
    expect(result.current.connection.error).toContain("504");
  });

  it("returns to live once a retry succeeds", async () => {
    let attempt = 0;
    const { result } = render(
      transport({
        subscribe: async function* () {
          attempt += 1;
          if (attempt === 1) throw new Error("stream failed: 504");
          yield { type: "message_delta", text: "back" } as AgentEvent;
          await new Promise<void>(() => {});
        },
      }),
    );

    await waitFor(() => expect(result.current.connection.state).toBe("reconnecting"));
    await waitFor(() => expect(result.current.connection.state).toBe("live"), { timeout: 4000 });
    expect(result.current.connection.error).toBeNull();
  });

  it("counts the attempts so a persistent outage is distinguishable from a blip", async () => {
    // One failed reconnect is noise; a climbing count is an outage, and the UI
    // needs to be able to say so rather than showing the same spinner forever.
    const { result } = render(
      transport({
        // eslint-disable-next-line require-yield
        subscribe: async function* () {
          throw new Error("stream failed: 502");
        },
      }),
    );

    // Backoff starts at 1s, so a second attempt lands well inside this — and
    // inside vitest's own 5s test ceiling.
    await waitFor(() => expect(result.current.connection.attempts).toBeGreaterThanOrEqual(2), {
      timeout: 3000,
    });
  });
});
