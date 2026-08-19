// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { realApi } from "./real";

afterEach(() => vi.unstubAllGlobals());

/**
 * `execShell` reads a failed response's body for its message, and that read has
 * to be bounded — a promise that never settles here leaves `TerminalPane` with
 * its entry stuck on "running" forever, because the `finally` that clears the
 * flag is never reached and `run()` early-returns while it is set.
 *
 * This file exists because nothing else covers it. `TerminalPane.test.tsx` mocks
 * `api.execShell` wholesale and `real.ts` had no test of its own, so the version
 * of this code that DID hang — a second, unbounded `await resp.text()` after the
 * bounded one — shipped with the whole suite green, twice.
 */
function stalling(status: number): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"detail":'));
        // …and never closes.
      },
    }),
    { status },
  );
}

describe("execShell error path", () => {
  it("rejects when the error body never finishes, instead of hanging the terminal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => stalling(502)),
    );
    const settled = await Promise.race([
      realApi.execShell("rca", "INC-1", ["ls"]).then(
        () => "resolved",
        (e: unknown) => e,
      ),
      new Promise((resolve) => setTimeout(() => resolve("HUNG"), 4000)),
    ]);
    expect(settled).toMatchObject({ status: 502 });
  });

  it("still puts the server's words in the message when the body does arrive", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("upstream connect error", { status: 502 })),
    );
    await expect(realApi.execShell("rca", "INC-1", ["ls"])).rejects.toMatchObject({
      message: "exec failed: 502 upstream connect error",
    });
  });

  it("carries the quota code so the pane can name which limit refused it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: { error: "sandbox_quota_exceeded" } }), {
            status: 507,
          }),
      ),
    );
    await expect(realApi.execShell("rca", "INC-1", ["ls"])).rejects.toMatchObject({
      status: 507,
      code: "sandbox_quota_exceeded",
    });
  });
});
