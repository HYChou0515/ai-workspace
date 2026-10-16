// @vitest-environment happy-dom
/**
 * The end of #942's wiring: the view file's `agents:` reaches the gate, and a
 * page's `callAgent` / `startTool` reach the item's own routes. Kept apart from
 * `WuiView.test.tsx` so the existing verbs' tests stay exactly as they were.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import type { FileContent } from "../../api/types";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { QueryWrap } from "../../test/queryWrapper";
import type { ViewSpec } from "../entity/types";
import { WUI_PROTOCOL } from "./protocol";
import { WuiView } from "./WuiView";

vi.mock("./served", () => ({ openServedWui: vi.fn(async () => null) }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The one request the page's verb made — the pane asks other routes too. */
function callTo(spy: ReturnType<typeof vi.fn>, part: string): [string, RequestInit] {
  const hit = (spy.mock.calls as unknown as [string, RequestInit][]).find(([u]) =>
    String(u).includes(part),
  );
  if (!hit) throw new Error(`no request to ${part}`);
  return hit;
}

const PAGE: Record<string, string> ={ "/sales/index.html": "<html><body>hi</body></html>" };

function svc(files: Record<string, string>): FileService {
  return {
    scopeId: "item1",
    caps: { write: true, delete: true },
    readFile: vi.fn(async (path: string): Promise<FileContent> => {
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return { kind: "text", path, size: files[path].length, text: files[path], encoding: "utf-8" };
    }),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

async function pageIn(spec: Partial<ViewSpec>, slug: string | null = "rca") {
  const view = (
    <FileServiceProvider value={svc(PAGE)}>
      <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "", ...spec } as ViewSpec} />
    </FileServiceProvider>
  );
  render(
    <QueryWrap>
      {slug ? <WorkspaceSlugProvider value={slug}>{view}</WorkspaceSlugProvider> : view}
    </QueryWrap>,
  );
  await waitFor(() => expect(document.querySelector("iframe")).toBeInTheDocument());
  const win = document.querySelector("iframe")?.contentWindow as Window;
  const replies: Record<string, unknown>[] = [];
  vi.spyOn(win, "postMessage").mockImplementation((m: unknown) =>
    replies.push(m as Record<string, unknown>),
  );
  const ask = (verb: string, args: Record<string, unknown>) =>
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { proto: WUI_PROTOCOL, id: "7", verb, args },
        source: win,
      }),
    );
  return { replies, ask };
}

describe("WuiView: sub-agents and jobs (#942)", () => {
  it("holds callAgent to the view file's `agents:`", async () => {
    const { replies, ask } = await pageIn({ agents: ["digger"] } as Partial<ViewSpec>, null);

    ask("callAgent", { name: "other", input: "x" });
    await waitFor(() => expect(replies).toHaveLength(1));
    expect(String(replies[0].error)).toMatch(/agents:/);

    // Declared, but no item to ask it in: a different refusal, which is the
    // proof the declaration itself was read.
    ask("callAgent", { name: "digger", input: "x" });
    await waitFor(() => expect(replies).toHaveLength(2));
    expect(String(replies[1].error)).not.toMatch(/agents:/);
  });

  it("asks a declared sub-agent on the item's own route", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ output: "ok", failed: false })));
    vi.stubGlobal("fetch", fetchSpy);
    const { replies, ask } = await pageIn({ agents: ["digger"] } as Partial<ViewSpec>);

    ask("callAgent", { name: "digger", input: "why?" });

    await waitFor(() => expect(replies).toHaveLength(1));
    expect(replies[0]).toMatchObject({ ok: true, value: { output: "ok", failed: false } });
    const [url] = callTo(fetchSpy, "/wui/");
    expect(url).toContain("/a/rca/items/item1/wui/agents/digger/call");
  });

  it("starts a declared tool as a job on the item's own route", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ job_id: "j1" })));
    vi.stubGlobal("fetch", fetchSpy);
    const { replies, ask } = await pageIn({ tools: ["lot-status"] } as Partial<ViewSpec>);

    ask("startTool", { name: "lot-status", args: { lot: "A1" } });

    await waitFor(() => expect(replies).toHaveLength(1));
    expect(replies[0]).toMatchObject({ ok: true, value: { job_id: "j1" } });
    const [url, init] = callTo(fetchSpy, "/wui/");
    expect(url).toMatch(/\/a\/rca\/items\/item1\/wui\/jobs$/);
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "tool",
      name: "lot-status",
      input: { lot: "A1" },
    });
  });
});
