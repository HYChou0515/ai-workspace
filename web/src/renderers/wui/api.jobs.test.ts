import { afterEach, describe, expect, it, vi } from "vitest";

import { itemCallAgent, itemJobs } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(...resps: Response[]) {
  const spy = vi.fn(async () => resps.shift() ?? new Response("{}"));
  vi.stubGlobal("fetch", spy);
  return spy;
}

const call = (spy: ReturnType<typeof stubFetch>, n = 0) =>
  spy.mock.calls[n] as unknown as [string, RequestInit | undefined];

describe("itemCallAgent (#942)", () => {
  it("posts the page's text to the item's sub-agent route, as a tool call posts its args", async () => {
    const spy = stubFetch(new Response(JSON.stringify({ output: "found it", failed: false })));

    const got = await itemCallAgent("rca", "i1")("digger", "why?");

    const [url, init] = call(spy);
    expect(url).toContain("/a/rca/items/i1/wui/agents/digger/call");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ input: "why?" });
    expect(got).toEqual({ output: "found it", failed: false });
  });

  it("escapes a sub-agent name rather than letting it shape the path", async () => {
    const spy = stubFetch(new Response(JSON.stringify({ output: "", failed: false })));
    await itemCallAgent("rca", "i1")("../../files", "");
    expect(call(spy)[0]).not.toContain("../..");
  });

  it("raises the server's own sentence", async () => {
    stubFetch(
      new Response(JSON.stringify({ detail: "digger took longer than 60s. Use startAgent." }), {
        status: 504,
      }),
    );
    await expect(itemCallAgent("rca", "i1")("digger", "x")).rejects.toThrow(/startAgent/);
  });
});

describe("itemJobs (#942)", () => {
  it("starts a job with its kind, name and input", async () => {
    const spy = stubFetch(new Response(JSON.stringify({ job_id: "j1" })));

    const got = await itemJobs("rca", "i1").start("tool", "lot-status", { lot: "A1" });

    const [url, init] = call(spy);
    expect(url).toMatch(/\/a\/rca\/items\/i1\/wui\/jobs$/);
    expect(JSON.parse(String(init?.body))).toEqual({
      kind: "tool",
      name: "lot-status",
      input: { lot: "A1" },
    });
    expect(got).toEqual({ job_id: "j1" });
  });

  it("reads and stops a job by id", async () => {
    const spy = stubFetch(
      new Response(JSON.stringify({ state: "running" })),
      new Response(JSON.stringify({ state: "stopped" })),
    );
    const jobs = itemJobs("rca", "i1");

    expect(await jobs.status("j1")).toEqual({ state: "running" });
    expect(await jobs.stop("j1")).toEqual({ state: "stopped" });

    expect(call(spy, 0)[0]).toMatch(/\/wui\/jobs\/j1$/);
    expect(call(spy, 0)[1]?.method ?? "GET").toBe("GET");
    expect(call(spy, 1)[0]).toMatch(/\/wui\/jobs\/j1\/stop$/);
    expect(call(spy, 1)[1]?.method).toBe("POST");
  });

  it("raises the server's own sentence when a job is refused", async () => {
    stubFetch(
      new Response(JSON.stringify({ detail: "This item already has 4 page jobs running." }), {
        status: 429,
      }),
    );
    await expect(itemJobs("rca", "i1").start("agent", "digger", "x")).rejects.toThrow(/4 page jobs/);
  });
});
