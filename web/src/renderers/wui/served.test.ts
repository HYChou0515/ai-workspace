import { afterEach, describe, expect, it, vi } from "vitest";

import { API_PREFIX } from "../../api/http";
import { openServedWui } from "./served";

afterEach(() => vi.unstubAllGlobals());

const PING = "wui-content";

/** A fetch answering the mint and the ping in turn. */
function stubFetch(mint: Response, ping: Response | Error) {
  const spy = vi.fn(async (url: string) => {
    if (url.endsWith("/wui/pass")) return mint;
    if (ping instanceof Error) throw ping;
    return ping;
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

const minted = () => new Response(JSON.stringify({ base: "/wui-content/TOK/" }));

describe("openServedWui", () => {
  it("mints a pass for the folder and hands back the address the frame loads from", async () => {
    const spy = stubFetch(minted(), new Response(PING));

    const base = await openServedWui("rca", "i 1", "/sales");

    expect(base).toBe(`${API_PREFIX}/wui-content/TOK/`);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_PREFIX}/a/rca/items/i%201/wui/pass`);
    expect(JSON.parse(String(init.body))).toEqual({ folder: "/sales" });
  });

  it("names the workspace root as / for a page with no folder", async () => {
    const spy = stubFetch(minted(), new Response(PING));

    await openServedWui("rca", "i1", "");

    const [, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ folder: "/" });
  });

  it("probes WITHOUT cookies, because that is how the frame will ask", async () => {
    /** Phase 1: an opaque-origin frame sends no cookie. A probe that sent one
     * would sail through a gateway that then turns every page request away. */
    const spy = stubFetch(minted(), new Response(PING));

    await openServedWui("rca", "i1", "/sales");

    const [url, init] = spy.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_PREFIX}/wui-content/TOK/__wui/ping`);
    expect(init.credentials).toBe("omit");
  });

  it.each([
    ["a gateway's login page", new Response("<html>Sign in</html>")],
    ["a route that has no runtime", new Response("no runtime", { status: 503 })],
    ["no answer at all", new TypeError("Failed to fetch")],
  ])("falls back to the single-page way when the probe meets %s", async (_why, ping) => {
    stubFetch(minted(), ping);

    expect(await openServedWui("rca", "i1", "/sales")).toBeNull();
  });

  it("falls back when there is no pass to be had — an older server, or a refusal", async () => {
    stubFetch(new Response("nope", { status: 404 }), new Response(PING));

    expect(await openServedWui("rca", "i1", "/sales")).toBeNull();
  });
});
