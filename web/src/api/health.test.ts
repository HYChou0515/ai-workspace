// @vitest-environment happy-dom
/**
 * `replayFetch`'s refusal message (#51 P6). It carries the server's own
 * sentence — FastAPI's string `detail` — and falls back to a generic one
 * otherwise. PR #811 folded the inline read into `detailSentence`; nothing
 * had ever reached this function through a test (`ReplayDialog.test.tsx`
 * constructs `ReplayError` directly), so the replacement is pinned here over
 * the shapes the old read met.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { realReplayApi, ReplayError } from "./health";

function answer(status: number, body: string | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status, headers: { "content-type": "application/json" } })),
  );
}

async function refusal(): Promise<ReplayError> {
  try {
    await realReplayApi.replayTurn({ source: "rca", thread_id: "t", message_index: 0 });
  } catch (err) {
    if (err instanceof ReplayError) return err;
    throw err;
  }
  throw new Error("expected a refusal");
}

afterEach(() => vi.unstubAllGlobals());

describe("replayFetch", () => {
  it("carries the server's sentence and its status", async () => {
    answer(409, JSON.stringify({ detail: "nothing to replay" }));
    const err = await refusal();
    expect(err.message).toBe("nothing to replay");
    expect(err.status).toBe(409);
  });

  it.each([
    ["an empty detail", JSON.stringify({ detail: "" })],
    ["a validation array", JSON.stringify({ detail: [{ loc: ["body"], msg: "x" }] })],
    ["no detail", "{}"],
    ["a non-JSON body", "<html>bad gateway</html>"],
    ["no body", null],
  ])("falls back to the generic message for %s", async (_what, body) => {
    // The array case is the one `detailSentence` exists for: the old inline
    // read cast the body as `{ detail?: string }` and passed an array through,
    // so the dialog showed "[object Object]" where the reason should be.
    answer(422, body);
    const err = await refusal();
    expect(err.message).toBe("replay failed: 422");
    expect(err.status).toBe(422);
  });
});
