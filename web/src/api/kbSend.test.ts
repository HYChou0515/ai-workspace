// @vitest-environment happy-dom
//
// #334: the KB chat turn request body must carry the per-message
// max_kb_searches pick, alongside reasoning_effort / enhancements / agent_name,
// so the composer's "Max searches" stepper isn't a silent no-op.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { realKbApi } from "./kb";

function installFetchSpy() {
  const bodies: string[] = [];
  const orig = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(String(init?.body ?? ""));
      return new Response('data: {"type": "done"}\n\n', {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    }),
  );
  return {
    bodies,
    restore: () => {
      vi.unstubAllGlobals();
      globalThis.fetch = orig;
    },
  };
}

describe("KB streamMessage request body", () => {
  let captured: ReturnType<typeof installFetchSpy>;
  beforeEach(() => {
    captured = installFetchSpy();
  });
  afterEach(() => captured.restore());

  it("carries the per-message max_kb_searches pick", async () => {
    const gen = realKbApi.streamMessage({
      chatId: "chat-1",
      content: "why voids?",
      reasoningEffort: "high",
      maxKbSearches: 0,
    });
    // Drain the async generator so the POST fires.
    for await (const _ of gen) {
      /* consume */
    }
    const body = JSON.parse(captured.bodies[0]!);
    expect(body.content).toBe("why voids?");
    expect(body.reasoning_effort).toBe("high");
    expect(body.max_kb_searches).toBe(0);
  });
});
