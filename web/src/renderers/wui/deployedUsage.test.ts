/**
 * docs/plan-wui-usage.md U7: a page opened by its DEPLOYED link names itself on
 * every use (`X-Wui-Deployed`), so the server counts it; the same page opened
 * inside the item sends nothing, and is not counted.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEPLOYED_HEADER, itemCallAgent, itemCallTool, itemJobs, reportOpened } from "./api";
import { itemRun } from "./run";

afterEach(() => vi.unstubAllGlobals());

const PAGE = "/報表/page.ai.yaml";

function stubFetch(body: unknown) {
  const spy = vi.fn(async () => new Response(JSON.stringify(body)));
  vi.stubGlobal("fetch", spy);
  return spy;
}

function headerOf(spy: ReturnType<typeof stubFetch>, at = 0): string | null {
  // A GET (a job's status) carries no init at all.
  const init = (spy.mock.calls[at] as unknown as [string, RequestInit | undefined])[1];
  return new Headers(init?.headers).get(DEPLOYED_HEADER);
}

describe("a deployed page names itself on every use", () => {
  it("callTool", async () => {
    const spy = stubFetch({ output: "", exit_code: 0 });
    await itemCallTool("rca", "i1", PAGE)("lot", {});
    expect(headerOf(spy)).toBe(encodeURIComponent(PAGE));
  });

  it("callAgent", async () => {
    const spy = stubFetch({ output: "", failed: false });
    await itemCallAgent("rca", "i1", PAGE)("helper", "hi");
    expect(headerOf(spy)).toBe(encodeURIComponent(PAGE));
  });

  it("startTool / startAgent", async () => {
    const spy = stubFetch({ job_id: "j1" });
    await itemJobs("rca", "i1", PAGE).start("tool", "lot", {});
    expect(headerOf(spy)).toBe(encodeURIComponent(PAGE));
  });

  it("startRun", async () => {
    const spy = vi.fn(async () => new Response("", { status: 200 }));
    vi.stubGlobal("fetch", spy);
    const events = itemRun("rca", "i1", PAGE)("nightly", {});
    for await (const _ of events) {
      // drain
    }
    expect(headerOf(spy as unknown as ReturnType<typeof stubFetch>)).toBe(encodeURIComponent(PAGE));
  });
});

describe("the same page inside the item sends nothing", () => {
  it("callTool, callAgent, the jobs and a run carry no header", async () => {
    const spy = stubFetch({ output: "", exit_code: 0, job_id: "j1", failed: false });
    await itemCallTool("rca", "i1")("lot", {});
    await itemCallAgent("rca", "i1")("helper", "hi");
    await itemJobs("rca", "i1").start("agent", "helper", "hi");
    expect([0, 1, 2].map((i) => headerOf(spy, i))).toEqual([null, null, null]);
  });

  it("asking a job's status or stopping it is not a use, so it never names the page", async () => {
    const spy = stubFetch({ state: "running", output: null, error: null });
    const jobs = itemJobs("rca", "i1", PAGE);
    await jobs.status("j1");
    await jobs.stop("j1");
    expect([0, 1].map((i) => headerOf(spy, i))).toEqual([null, null]);
  });
});

describe("reportOpened", () => {
  it("posts the page's path to the item's opened route", async () => {
    const spy = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", spy);

    await reportOpened("rca", "i1", PAGE);

    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/a/rca/items/i1/wui/opened");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ path: PAGE });
  });

  it("never throws: a page must open whether or not it could be counted", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );

    await expect(reportOpened("rca", "i1", PAGE)).resolves.toBeUndefined();
  });
});
