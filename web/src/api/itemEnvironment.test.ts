/**
 * The item's own environment: what it costs, whether it is running, and the
 * size somebody set for it.
 *
 * Kept apart from `myResources` because the two answer different questions for
 * different people — that one is "what am I holding across everything", this
 * one is "what is THIS item doing", and a collaborator may see the second
 * without seeing the first.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { itemEnvironmentApi } from "./itemEnvironment";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("itemEnvironmentApi.get", () => {
  it("asks the item's own route, not the person's", async () => {
    vi.mocked(fetch).mockResolvedValue(
      ok({
        running: false,
        stated_cpu_cores: null,
        stated_memory_bytes: null,
        effective_cpu_cores: 2,
        effective_memory_bytes: 1024,
      }),
    );

    await itemEnvironmentApi.get("rca", "item-1");

    const url = String(vi.mocked(fetch).mock.calls[0][0]);
    expect(url).toContain("/a/rca/items/item-1/environment");
    expect(url).not.toContain("/me/resources");
  });

  it("keeps stated and effective apart", async () => {
    // The clamped case: somebody asked for 8 cores and may have 2. Collapsing
    // these into one number is how a setting comes to disagree with what it
    // does, with nothing on screen to explain the difference.
    vi.mocked(fetch).mockResolvedValue(
      ok({
        running: true,
        stated_cpu_cores: 8,
        stated_memory_bytes: null,
        effective_cpu_cores: 2,
        effective_memory_bytes: null,
      }),
    );

    const got = await itemEnvironmentApi.get("rca", "item-1");

    expect(got.statedCpuCores).toBe(8);
    expect(got.effectiveCpuCores).toBe(2);
    expect(got.running).toBe(true);
  });

  it("throws on a refusal instead of resolving with nothing", async () => {
    // The failure `myResources.closeEnvironment` was fixed for: a swallowed
    // status resolves exactly like a success, and the panel renders an empty
    // environment as though that were the answer.
    vi.mocked(fetch).mockResolvedValue(new Response("nope", { status: 403 }));

    await expect(itemEnvironmentApi.get("rca", "item-1")).rejects.toThrow();
  });
});

describe("itemEnvironmentApi.setSize", () => {
  it("sends the size to the item's own resources route", async () => {
    vi.mocked(fetch).mockResolvedValue(ok({ cpu_cores: 1.5, memory_bytes: null }));

    await itemEnvironmentApi.setSize("rca", "item-1", { cpuCores: 1.5, memory: null });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/a/rca/items/item-1/resources");
    expect((init as RequestInit).method).toBe("PUT");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      cpu_cores: 1.5,
      memory: null,
    });
  });

  it("sends null to clear rather than omitting the key", async () => {
    // Omitting it would read as "leave this dimension alone"; `null` is how
    // "nobody has said" is expressed, and it is what restores the resolved
    // default instead of storing a number.
    vi.mocked(fetch).mockResolvedValue(ok({ cpu_cores: null, memory_bytes: null }));

    await itemEnvironmentApi.setSize("rca", "item-1", { cpuCores: null, memory: null });

    const body = JSON.parse(String((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body));
    expect(body).toHaveProperty("cpu_cores", null);
    expect(body).toHaveProperty("memory", null);
  });
});
