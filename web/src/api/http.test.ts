import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, apiFetch } from "./http";

describe("apiFetch", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("prefixes the path with API_BASE and forwards init", async () => {
    const spy = vi.fn(async () => new Response("ok"));
    vi.stubGlobal("fetch", spy);

    await apiFetch("/kb/collections", { method: "POST" });

    expect(spy).toHaveBeenCalledWith(`${API_BASE}/kb/collections`, { method: "POST" });
  });

  it("API_BASE has no trailing slash (so it concatenates cleanly)", () => {
    // default deploy base is "/" → API_BASE === "" → apiFetch("/x") === fetch("/x")
    expect(API_BASE.endsWith("/")).toBe(false);
  });
});
