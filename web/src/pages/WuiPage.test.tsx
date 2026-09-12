// @vitest-environment happy-dom
/**
 * A WUI at its own URL.
 *
 * The decision that makes this small: **whoever opens the link must already be
 * able to see the item.** So there is no new permission model, no export, and no
 * second server — the same login, the same file service, the same assembler and
 * the same sandbox/CSP envelope, rendered without the workspace shell around it.
 *
 * What it is FOR: a colleague who is already in the item should not have to go
 * hunting through a file tree. The URL is a shortcut, not a grant.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { WUI_PROTOCOL } from "../renderers/wui/protocol";
import { QueryWrap } from "../test/queryWrapper";
import { WuiPage } from "./WuiPage";

const YAML = "view: wui\ntitle: Scrap review\n";

function renderAt(path: string, readFile: (p: string) => Promise<unknown>) {
  vi.mock("../api/fileService", async () => {
    const actual = await vi.importActual<Record<string, unknown>>("../api/fileService");
    return actual;
  });
  return render(
    <QueryWrap>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/w/:slug/:itemId/*" element={<WuiPage makeService={() => makeFs(readFile)} />} />
        </Routes>
      </MemoryRouter>
    </QueryWrap>,
  );
}

function makeFs(readFile: (p: string) => Promise<unknown>) {
  return {
    scopeId: "i1",
    caps: { write: true, delete: true, download: true },
    readFile,
    listFiles: async () => [],
    listDirs: async () => [],
    listTree: async () => ({ items: [], dirs: [] }),
    writeFile: async () => {},
    deleteFile: async () => {},
    fileDownloadUrl: (p: string) => `/api/files${p}`,
  } as unknown as import("../api/fileService").FileService;
}

describe("WuiPage", () => {
  it("renders the page named by the URL", async () => {
    // The view file AND the entry it names: the assembler inlines the folder, so
    // a double that serves only the yaml renders the assembler's error rather
    // than a page — and the test would then be asserting on the wrong thing.
    const files: Record<string, string> = {
      "/scrap-review/page.ai.yaml": YAML,
      "/scrap-review/index.html": "<!doctype html><p>hello</p>",
    };
    const readFile = vi.fn(async (path: string) => {
      const text = files[path];
      if (text === undefined) throw new Error(`not found: ${path}`);
      return { kind: "text", path, text, size: text.length, encoding: "utf-8" };
    });

    renderAt("/w/rca/i1/scrap-review/page.ai.yaml", readFile);

    // The frame is the page. Its title is what the view file said.
    await waitFor(() => expect(screen.getByTitle("Scrap review")).toBeTruthy());
  });

  it("gives the page the slug, so its tools still answer here", async () => {
    /**
     * `WuiView` reads the slug from a CONTEXT, not from the route. Outside the
     * workspace shell nothing provides it and the default is the empty string —
     * at which point `callTool` is null and every tool button on the page does
     * nothing, without a word.
     *
     * Asserted through the real path — the tool request the page's own frame
     * makes — rather than by peeking at the context, so a production seam added
     * just for this test cannot make it pass. It used to be asserted through
     * the build request instead; a reader's page no longer builds (below), and
     * the tools are the one thing a reader is promised to keep.
     */
    const yaml = `${YAML}tools: [lot-status]\n`;
    const files: Record<string, string> = {
      "/scrap-review/page.ai.yaml": yaml,
      "/scrap-review/index.html": "<!doctype html><p>hello</p>",
      // Buildable: an author's page here WOULD rebuild on open.
      "/scrap-review/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
    };
    const readFile = vi.fn(async (path: string) => {
      const text = files[path];
      if (text === undefined) throw new Error(`not found: ${path}`);
      return { kind: "text", path, text, size: text.length, encoding: "utf-8" };
    });

    const urls: string[] = [];
    const realFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return new Response(JSON.stringify({ ok: true, result: {} }), {
        headers: { "content-type": "application/json" },
      });
    }) as typeof globalThis.fetch;

    try {
      renderAt("/w/rca/i1/scrap-review/page.ai.yaml", readFile);
      await waitFor(() => expect(screen.getByTitle("Scrap review")).toBeTruthy());

      const win = (screen.getByTitle("Scrap review") as HTMLIFrameElement).contentWindow as Window;
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { proto: WUI_PROTOCOL, id: "1", verb: "callTool", args: { name: "lot-status", args: {} } },
          source: win,
        }),
      );
      await waitFor(() => expect(urls.some((u) => u.includes("/wui/tools/"))).toBe(true));
    } finally {
      globalThis.fetch = realFetch;
    }

    // The slug from the URL, not the empty-string default.
    expect(urls.find((u) => u.includes("/wui/tools/"))).toContain("/a/rca/items/i1/wui/tools/lot-status/call");
    // And nothing was built — nor even looked at for a build — on the
    // reader's account (`WuiView.test.tsx` "never rebuilds on a reader's
    // account" is the pane-level pin; this is the route-level one).
    expect(urls.filter((u) => u.includes("/wui/build"))).toHaveLength(0);
    expect(readFile).not.toHaveBeenCalledWith("/scrap-review/package.json");
  });

  it("says so plainly when the view file is not there", async () => {
    /**
     * The reader of this URL cannot open a console and did not choose the path —
     * somebody sent them the link. "Not found" has to be a sentence naming what
     * was looked for, or they have nothing to forward back.
     */
    const readFile = vi.fn(async (path: string) => {
      throw new Error(`not found: ${path}`);
    });

    renderAt("/w/rca/i1/gone/page.ai.yaml", readFile);

    // The path AND which of the two wrong things happened. Both branches mention
    // the path, so asserting only that let a mutation deleting this one pass —
    // and "this is not a page" sends the reader to fix something that is not
    // broken, when the file simply is not there.
    await waitFor(() => {
      const said = screen.getByRole("alert").textContent ?? "";
      expect(said).toContain("/gone/page.ai.yaml");
      expect(said).toMatch(/no file/i);
    });
  });

  it("refuses a file that is not a WUI", async () => {
    /**
     * `/w/...` is not a general file viewer. Pointing it at a board or a plain
     * markdown file must say so rather than rendering an empty frame — the
     * failure would otherwise look like a broken page instead of a wrong link.
     */
    const board = "view: board\ntitle: Not a page\n";
    const readFile = vi.fn(async (path: string) => ({
      kind: "text",
      path,
      text: board,
      size: board.length,
      encoding: "utf-8",
    }));

    renderAt("/w/rca/i1/board/board.ai.yaml", readFile);

    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/not a page/i));
  });
});

describe("WuiPage: what a reader is handed", () => {
  /**
   * Somebody followed a link. They have nothing to rebuild, nobody to tell and
   * nothing to pick — the toolbar is the author's, and every control on it was
   * reaching the reader (docs/plan-wui-deploy.md, P1).
   *
   * Each control is asserted by its own name rather than "no toolbar": one
   * control left behind is exactly the leak this guards against, and a
   * toolbar-shaped assertion would not see it. The positive control is
   * `WuiView.test.tsx`, which presses every one of these in workspace chrome.
   */
  it("shows the page and none of the author's controls", async () => {
    const files: Record<string, string> = {
      "/scrap-review/page.ai.yaml": YAML,
      "/scrap-review/index.html": "<!doctype html><p>hello</p>",
      // Buildable, so that Rebuild / Auto-rebuild WOULD be offered to an author.
      "/scrap-review/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
    };
    const readFile = vi.fn(async (path: string) => {
      const text = files[path];
      if (text === undefined) throw new Error(`not found: ${path}`);
      return { kind: "text", path, text, size: text.length, encoding: "utf-8" };
    });

    renderAt("/w/rca/i1/scrap-review/page.ai.yaml", readFile);

    // The page itself is there — so an empty screen cannot pass the rest.
    await waitFor(() => expect(screen.getByTitle("Scrap review")).toBeTruthy());
    // Every control the author's chrome can draw — the build-output toggle
    // and Deploy included (review round 1 found them missing from this list
    // while its docstring promised one-by-one coverage).
    for (const name of [
      /refresh/i,
      /rebuild/i,
      /auto-rebuild/i,
      /report a problem/i,
      /tell the agent/i,
      /^deploy$/i,
      /build output/i,
    ]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
      expect(screen.queryByRole("switch", { name })).toBeNull();
    }
  });

  it("does not call a page unpublished when it merely could not be read", async () => {
    /**
     * Review round 1: every `built.error` in viewer chrome was rendered as
     * "not published yet" — a dropped connection, a 403, a 500, an entry that
     * is not HTML. The reader then told the author the page was never
     * published, and the author re-deployed a page that was fine.
     * `WuiEntryMissing` carries a reason for exactly these; only a genuine
     * absence (no reason) is "not published".
     */
    const files: Record<string, string> = { "/scrap-review/page.ai.yaml": YAML };
    const readFile = vi.fn(async (path: string) => {
      const text = files[path];
      if (text !== undefined) return { kind: "text", path, text, size: text.length, encoding: "utf-8" };
      // The three-outcome reader classes a TypeError as "failed" — the
      // workspace could not be reached — not as "missing".
      throw new TypeError("Failed to fetch");
    });

    renderAt("/w/rca/i1/scrap-review/page.ai.yaml", readFile);

    const said = await screen.findByRole("status");
    expect(said).toHaveTextContent(/could not reach the workspace/i);
    expect(said).not.toHaveTextContent(/not been published/i);
  });

  it("opens a folder whose name had to be encoded into the address", async () => {
    /**
     * The other half of `WuiView.test.tsx` "encodes a folder name…": the
     * address Deploy hands over must land on the file it was made from. A
     * space or a CJK name is the ordinary case, not the edge.
     */
    const files: Record<string, string> = {
      "/報告 v2/page.ai.yaml": YAML,
      "/報告 v2/index.html": "<!doctype html><p>hello</p>",
    };
    const readFile = vi.fn(async (path: string) => {
      const text = files[path];
      if (text === undefined) throw new Error(`not found: ${path}`);
      return { kind: "text", path, text, size: text.length, encoding: "utf-8" };
    });

    renderAt("/w/rca/i1/%E5%A0%B1%E5%91%8A%20v2/page.ai.yaml", readFile);

    await waitFor(() => expect(screen.getByTitle("Scrap review")).toBeTruthy());
    expect(readFile).toHaveBeenCalledWith("/報告 v2/page.ai.yaml");
  });

  it("says the page is not published yet when there is nothing built", async () => {
    /**
     * A buildable page nobody has built: `dist/index.html` is not there. An
     * author sees the file's name in red and a Rebuild button (the workspace
     * test "names the missing file…" is the positive control). A reader can do
     * neither — the sentence has to say what state the page is in, not which
     * file is missing, or a blank frame reads as a broken page rather than an
     * unpublished one.
     */
    const files: Record<string, string> = {
      "/scrap-review/page.ai.yaml": `${YAML}entry: dist/index.html\n`,
      "/scrap-review/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
    };
    const readFile = vi.fn(async (path: string) => {
      const text = files[path];
      if (text === undefined) throw new Error(`not found: ${path}`);
      return { kind: "text", path, text, size: text.length, encoding: "utf-8" };
    });

    renderAt("/w/rca/i1/scrap-review/page.ai.yaml", readFile);

    const said = await screen.findByRole("status");
    expect(said).toHaveTextContent(/not been published/i);
    expect(said).not.toHaveTextContent("index.html");
    expect(document.querySelector("iframe")).toBeNull();
  });
});
