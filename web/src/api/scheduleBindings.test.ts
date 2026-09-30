/** "Run as me" (`plan-wui-viewer-login` Q7/Q12): the page bar's schedule list. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { scheduleBindingsApi } from "./scheduleBindings";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

describe("scheduleBindingsApi", () => {
  it("lists one schedules file's rows with who each runs as", async () => {
    const row = { trigger_id: "k", run: "r", describe: "daily", bound_to: "", mine: false };
    vi.mocked(fetch).mockResolvedValue(ok({ rows: [row] }));

    expect(await scheduleBindingsApi.list("rca", "i1", "/page/schedules.json")).toEqual([row]);
    const url = String(vi.mocked(fetch).mock.calls[0][0]);
    expect(url).toContain("/a/rca/items/i1/schedule-bindings?path=%2Fpage%2Fschedules.json");
  });

  it("binds the caller with PUT and unbinds with DELETE", async () => {
    vi.mocked(fetch).mockResolvedValue(ok({}));
    await scheduleBindingsApi.bind("rca", "i1", "/p/schedules.json", "key/1");
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
    await scheduleBindingsApi.unbind("rca", "i1", "/p/schedules.json", "key/1");

    const [bindUrl, bindInit] = vi.mocked(fetch).mock.calls[0];
    expect(bindInit?.method).toBe("PUT");
    expect(String(bindUrl)).toContain("/schedule-bindings/key%2F1?path=");
    expect(vi.mocked(fetch).mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("surfaces a refusal", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: "no" }), { status: 403 }));
    await expect(scheduleBindingsApi.bind("rca", "i1", "/p/schedules.json", "k")).rejects.toThrow();
  });
});
