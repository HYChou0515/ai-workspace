import { afterEach, describe, expect, it, vi } from "vitest";

import { itemCallTool } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(resp: Response) {
  const spy = vi.fn(async () => resp);
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("itemCallTool", () => {
  it("posts the tool's arguments to the item's own route", async () => {
    const spy = stubFetch(new Response(JSON.stringify({ output: "{}", exit_code: 0 })));

    await itemCallTool("rca", "i1")("lot-status", { lot: "A1" });

    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/a/rca/items/i1/wui/tools/lot-status/call");
    expect(JSON.parse(String(init.body))).toEqual({ args: { lot: "A1" } });
  });

  it("escapes a tool name rather than letting it shape the path", async () => {
    const spy = stubFetch(new Response(JSON.stringify({ output: "", exit_code: 0 })));

    await itemCallTool("rca", "i1")("../../files/notes.md", {});

    const [url] = spy.mock.calls[0] as unknown as [string];
    expect(url).not.toContain("../..");
  });

  it("raises the server's own sentence, which is what the reader is shown", async () => {
    // The refusal text goes into the page's error panel and gets forwarded to
    // the agent, so replacing it with a status code loses the only useful part.
    stubFetch(
      new Response(JSON.stringify({ detail: "This app does not offer lot-status to its pages." }), {
        status: 403,
      }),
    );

    await expect(itemCallTool("rca", "i1")("lot-status", {})).rejects.toThrow(/does not offer/);
  });

  it("still says something when the failure carries no sentence", async () => {
    stubFetch(new Response("<html>gateway</html>", { status: 502 }));

    await expect(itemCallTool("rca", "i1")("lot-status", {})).rejects.toThrow(/502/);
  });

  it("does not hand the reader a stringified validation array", async () => {
    // FastAPI's 422 `detail` is a LIST, and passing it through produced
    // "Error: [object Object]" in the page and in the report forwarded to the
    // agent — the one outcome this branch exists to prevent.
    stubFetch(
      new Response(JSON.stringify({ detail: [{ loc: ["body", "args"], msg: "not a dict" }] }), {
        status: 422,
      }),
    );

    await expect(itemCallTool("rca", "i1")("lot-status", {})).rejects.toThrow(/422/);
    await expect(itemCallTool("rca", "i1")("lot-status", {})).rejects.not.toThrow(/object Object/);
  });
});
