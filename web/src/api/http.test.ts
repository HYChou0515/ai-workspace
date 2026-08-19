import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, API_PREFIX, apiFetch, errorInfo } from "./http";

/** A response whose body is delivered but never closed — an ingress cutting a
 *  stream mid-flight, which is the shape that turns an await into a hang. */
function stalling(status: number, prefix = '{"detail":'): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(prefix));
      },
    }),
    { status },
  );
}

describe("errorInfo", () => {
  it("reads the body ONCE and hands back its text, so no caller needs a second read", async () => {
    const resp = new Response(JSON.stringify({ detail: { error: "sandbox_quota_exceeded" } }), {
      status: 507,
    });
    const info = await errorInfo(resp);
    expect(info.code).toBe("sandbox_quota_exceeded");
    // `execShell` puts the raw body in its message. Taking it from here rather
    // than from a second `resp.text()` is what keeps that read bounded too — a
    // deadline on the first read buys nothing if the next line is unbounded.
    expect(info.text).toContain("sandbox_quota_exceeded");
  });

  it("gives up on a body that never finishes instead of waiting forever", async () => {
    const settled = await Promise.race([
      errorInfo(stalling(502)).then(() => "settled"),
      new Promise((resolve) => setTimeout(() => resolve("HUNG"), 3000)),
    ]);
    expect(settled).toBe("settled");
  });

  it("keeps a non-JSON body as text rather than discarding it", async () => {
    const info = await errorInfo(new Response("upstream connect error", { status: 502 }));
    expect(info.code).toBeUndefined();
    expect(info.text).toBe("upstream connect error");
  });
});

describe("apiFetch", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("prefixes the path with API_BASE and forwards init", async () => {
    const spy = vi.fn(async () => new Response("ok"));
    vi.stubGlobal("fetch", spy);

    await apiFetch("/kb/collections", { method: "POST" });

    expect(spy).toHaveBeenCalledWith(`${API_PREFIX}/kb/collections`, { method: "POST" });
  });

  it("API_BASE has no trailing slash (so it concatenates cleanly)", () => {
    // default deploy base is "/" → API_BASE === "" → apiFetch("/x") === fetch("/api/x")
    expect(API_BASE.endsWith("/")).toBe(false);
  });

  it("API_PREFIX is the deploy base + /api — every backend URL roots here (#177)", () => {
    expect(API_PREFIX).toBe(`${API_BASE}/api`);
  });
});
