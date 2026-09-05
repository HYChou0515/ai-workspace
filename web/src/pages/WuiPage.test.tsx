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
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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

  it("gives the page the slug, so rebuilding still works here", async () => {
    /**
     * `WuiView` reads the slug from a CONTEXT, not from the route. Outside the
     * workspace shell nothing provides it and the default is the empty string —
     * at which point auto-rebuild never fires (the page shows a stale `dist/`)
     * and `callTool` is null (every tool button does nothing). Neither says a
     * word.
     *
     * Asserted through the real path — the build request the page actually makes
     * — rather than by peeking at the context, so a production seam added just
     * for this test cannot make it pass.
     */
    const files: Record<string, string> = {
      "/scrap-review/page.ai.yaml": YAML,
      "/scrap-review/index.html": "<!doctype html><p>hello</p>",
      // A built page: this is what makes auto-rebuild fire at all.
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
      return new Response("data: {\"type\":\"done\",\"exit_code\":0}\n\n", {
        headers: { "content-type": "text/event-stream" },
      });
    }) as typeof globalThis.fetch;

    try {
      renderAt("/w/rca/i1/scrap-review/page.ai.yaml", readFile);
      await waitFor(() => expect(urls.some((u) => u.includes("/wui/build"))).toBe(true));
    } finally {
      globalThis.fetch = realFetch;
    }

    // The slug from the URL, not the empty-string default.
    expect(urls.find((u) => u.includes("/wui/build"))).toContain("/a/rca/items/i1/");
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
