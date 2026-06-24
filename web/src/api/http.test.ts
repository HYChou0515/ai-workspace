import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, API_PREFIX, apiFetch } from "./http";

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
