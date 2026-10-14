// @vitest-environment happy-dom
/**
 * The bridge's new verbs (`docs/plan-wui-call-agent.md`, #942): asking a
 * sub-agent, and starting a tool or a sub-agent as a job. Kept apart from
 * `bridge.test.ts` so the existing verbs' tests stay exactly as they were (D10).
 */
import { describe, expect, it, vi } from "vitest";

import type { FileService } from "../../api/fileService";
import { dispatchWuiRequest, type BridgeContext, type PageJobs } from "./bridge";
import { WUI_PROTOCOL, type WuiRequest } from "./protocol";

function jobs(over: Partial<PageJobs> = {}): PageJobs {
  return {
    start: vi.fn(async () => ({ job_id: "j1" })),
    status: vi.fn(async () => ({ state: "running" as const })),
    stop: vi.fn(async () => ({ state: "stopped" as const })),
    ...over,
  };
}

function ctx(over: Partial<BridgeContext> = {}): BridgeContext {
  return {
    fs: { scopeId: "item1", caps: { write: true, delete: true } } as unknown as FileService,
    folder: "/sales",
    openFile: null,
    me: "alice",
    declaredTools: ["lot-status"],
    callTool: null,
    declaredWorkflows: [],
    startRun: null,
    openLogin: null,
    onRunEvent: () => {},
    declaredAgents: ["digger"],
    callAgent: vi.fn(async () => ({ output: "found it", failed: false })),
    jobs: jobs(),
    ...over,
  };
}

const req = (verb: string, args: Record<string, unknown> = {}): WuiRequest => ({
  proto: WUI_PROTOCOL,
  id: "1",
  verb,
  args,
});

describe("asking a sub-agent from a page", () => {
  it("asks a declared sub-agent and hands back its answer as it stands", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("callAgent", { name: "digger", input: "why?" }), c);
    expect(res).toMatchObject({ ok: true, value: { output: "found it", failed: false } });
    expect(c.callAgent).toHaveBeenCalledWith("digger", "why?");
  });

  it("refuses a sub-agent the page did not declare, and says where to declare it", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("callAgent", { name: "other", input: "x" }), c);
    expect(res).toMatchObject({ ok: false });
    expect(res.ok ? "" : res.error).toMatch(/agents:/);
    expect(c.callAgent).not.toHaveBeenCalled();
  });

  it("turns a refused ask into a sentence a person can read", async () => {
    const c = ctx({
      callAgent: vi.fn(async () => {
        throw new Error("digger took longer than 60s, so it was stopped. Use startAgent.");
      }),
    });
    const res = await dispatchWuiRequest(req("callAgent", { name: "digger", input: "x" }), c);
    expect(res).toMatchObject({ ok: false, error: expect.stringMatching(/startAgent/) });
  });

  it("says so where sub-agents cannot be asked", async () => {
    const res = await dispatchWuiRequest(
      req("callAgent", { name: "digger", input: "x" }),
      ctx({ callAgent: null }),
    );
    expect(res).toMatchObject({ ok: false });
  });
});

describe("a verb that does not exist", () => {
  it("is still named and refused, now that `callAgent` is a real verb", async () => {
    // `bridge.test.ts` used `callAgent` as its example of an invented verb; it
    // now reaches the real branch, so the fallback needs a name nobody uses.
    const res = await dispatchWuiRequest(req("summonDragon", {}), ctx());
    expect(res).toMatchObject({ ok: false });
    expect(res.ok ? "" : res.error).toBe('This page asked for "summonDragon", which a WUI cannot do.');
  });
});

describe("starting a job from a page", () => {
  it("starts a declared tool, with its args", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(
      req("startTool", { name: "lot-status", args: { lot: "A1" } }),
      c,
    );
    expect(res).toMatchObject({ ok: true, value: { job_id: "j1" } });
    expect(c.jobs!.start).toHaveBeenCalledWith("tool", "lot-status", { lot: "A1" });
  });

  it("starts a declared sub-agent, with its text", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("startAgent", { name: "digger", input: "go" }), c);
    expect(res).toMatchObject({ ok: true, value: { job_id: "j1" } });
    expect(c.jobs!.start).toHaveBeenCalledWith("agent", "digger", "go");
  });

  it("holds startTool to `tools:` and startAgent to `agents:`, as the calls are", async () => {
    const c = ctx();
    const tool = await dispatchWuiRequest(req("startTool", { name: "other", args: {} }), c);
    const agent = await dispatchWuiRequest(req("startAgent", { name: "other", input: "" }), c);
    expect(tool.ok ? "" : tool.error).toMatch(/tools:/);
    expect(agent.ok ? "" : agent.error).toMatch(/agents:/);
    expect(c.jobs!.start).not.toHaveBeenCalled();
  });

  it("reads and stops a job by its id", async () => {
    const c = ctx();
    expect(await dispatchWuiRequest(req("jobStatus", { job: "j1" }), c)).toMatchObject({
      ok: true,
      value: { state: "running" },
    });
    expect(await dispatchWuiRequest(req("stopJob", { job: "j1" }), c)).toMatchObject({
      ok: true,
      value: { state: "stopped" },
    });
    expect(c.jobs!.status).toHaveBeenCalledWith("j1");
    expect(c.jobs!.stop).toHaveBeenCalledWith("j1");
  });

  it("turns a refused start into a sentence a person can read", async () => {
    const c = ctx({
      jobs: jobs({
        start: vi.fn(async () => {
          throw new Error("This item already has 4 page jobs running.");
        }),
      }),
    });
    const res = await dispatchWuiRequest(req("startAgent", { name: "digger", input: "x" }), c);
    expect(res).toMatchObject({ ok: false, error: expect.stringMatching(/4 page jobs/) });
  });

  it("says so where jobs cannot be started", async () => {
    const res = await dispatchWuiRequest(
      req("startTool", { name: "lot-status", args: {} }),
      ctx({ jobs: null }),
    );
    expect(res).toMatchObject({ ok: false });
  });
});
