// @vitest-environment happy-dom
//
// The RCA turn request body — the composer picker's choices must reach
// the wire: reasoning_effort and (new) the knowledge-search depth
// `enhancements`, which the BE forwards to ask_knowledge_base's KB
// sub-agent. Pinned here so a body-shape regression can't silently
// turn the picker into a no-op.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

function installFetchSpy() {
  const bodies: string[] = [];
  const orig = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(String(init?.body ?? ""));
      return new Response('data: {"type": "run_done"}\n\n', {
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

describe("sendMessage request body", () => {
  let captured: ReturnType<typeof installFetchSpy>;
  beforeEach(() => {
    captured = installFetchSpy();
  });
  afterEach(() => {
    captured.restore();
  });

  it("carries reasoning_effort and the knowledge-search depth", async () => {
    // #43: POST enqueues the turn (no longer streams) — the body shape is
    // unchanged, so the composer picker's choices still reach the wire.
    await realApi.sendMessage({
      slug: "rca",
      investigationId: "inv-1",
      content: "why is zone 3 hot?",
      reasoningEffort: "high",
      enhancements: { expand: 3, hyde: null, rerank: false },
      maxKbSearches: 2,
    });
    const body = JSON.parse(captured.bodies[0]!);
    expect(body.content).toBe("why is zone 3 hot?");
    expect(body.reasoning_effort).toBe("high");
    expect(body.enhancements).toEqual({ expand: 3, hyde: null, rerank: false });
    // #334: the per-message kb_search-count pick reaches the wire.
    expect(body.max_kb_searches).toBe(2);
  });

  it("carries the composer's attached image paths (so a VLM main model sees them)", async () => {
    // A vision main model reads attached images inline; the BE reads these
    // workspace paths and inlines the images into the turn. The paths must
    // reach the wire structurally (not only prepended as text).
    await realApi.sendMessage({
      slug: "rca",
      investigationId: "inv-1",
      content: "what defect is this?",
      imagePaths: ["/uploads/shot.png", "/uploads/chart.png"],
    });
    const body = JSON.parse(captured.bodies[0]!);
    expect(body.image_paths).toEqual(["/uploads/shot.png", "/uploads/chart.png"]);
  });
});
