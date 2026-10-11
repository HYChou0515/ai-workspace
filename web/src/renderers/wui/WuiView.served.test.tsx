// @vitest-environment happy-dom
/**
 * The pane with a page served from its own address (`docs/plan-wui-multipage.md`,
 * Phase 4). Whether the deployment CAN serve it is `openServedWui`'s question,
 * answered in its own tests; here it is answered by hand, both ways.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import { HttpError } from "../../api/http";
import type { FileContent } from "../../api/types";
import { OpenFileProvider } from "../../hooks/openFile";
import { autoBuildScope, setWuiAutoBuild } from "../../lib/wuiAutoBuild";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { makeTestQueryClient, QueryWrap } from "../../test/queryWrapper";
import type { ViewSpec } from "../entity/types";
import { WUI_PROTOCOL } from "./protocol";
import { openServedWui } from "./served";
import { WuiView, type WuiChrome } from "./WuiView";

const served = vi.hoisted(() => ({ base: "http://localhost:3000/api/wui-content/TOK/" as string | null }));
vi.mock("./served", () => ({ openServedWui: vi.fn(async () => served.base) }));

const BASE = "http://localhost:3000/api/wui-content/TOK/";
const ORIGIN = "http://localhost:3000";

const text = (path: string, body: string): FileContent => ({
  kind: "text",
  path,
  size: body.length,
  text: body,
  encoding: "utf-8",
});

function svc(files: Record<string, string>): FileService {
  return {
    scopeId: "item1",
    caps: { write: true, delete: true },
    readFile: vi.fn(async (path: string) => {
      if (!(path in files)) throw new HttpError(404, `read ${path} failed: 404`);
      return text(path, files[path]);
    }),
    writeFile: vi.fn(),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

const SITE = {
  "/sales/index.html": "<html><head><link rel=stylesheet href=s.css></head><body>home</body></html>",
  "/sales/s.css": "body{}",
};

type PaneOpts = {
  files?: Record<string, string>;
  spec?: Partial<ViewSpec>;
  chrome?: WuiChrome;
  openFile?: (p: string) => void;
  path?: string;
};

function renderPane(opts: PaneOpts = {}) {
  const fs = svc(opts.files ?? { ...SITE });
  const tree = (o: PaneOpts) => {
    const view = (
      <WuiView
        path={o.path ?? "/sales/page.ai.yaml"}
        spec={{ view: "wui", entity: "", ...o.spec } as ViewSpec}
        chrome={o.chrome}
      />
    );
    return (
      <WorkspaceSlugProvider value="rca">
        <FileServiceProvider value={fs}>
          {o.openFile ? <OpenFileProvider value={o.openFile}>{view}</OpenFileProvider> : view}
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    );
  };
  const client = makeTestQueryClient();
  const utils = render(<QueryWrap client={client}>{tree(opts)}</QueryWrap>);
  /** Show another view file in the same pane — the same folder keeps the instance. */
  const showView = (o: PaneOpts) => utils.rerender(<QueryWrap client={client}>{tree({ ...opts, ...o })}</QueryWrap>);
  return { ...utils, fs, showView };
}

const frame = () => document.querySelector("iframe");

/** Let every effect a verdict sets off run, before asserting nothing changed —
 * a remount that lands a tick after the last awaited text is still a remount. */
const settle = () => act(async () => {
  await new Promise((r) => setTimeout(r, 100));
});

async function framed(opts: Parameters<typeof renderPane>[0] = {}) {
  const r = renderPane(opts);
  await waitFor(() => expect(frame()).toBeInTheDocument());
  const win = frame()!.contentWindow as Window;
  const replies: unknown[] = [];
  vi.spyOn(win, "postMessage").mockImplementation((m: unknown) => replies.push(m));
  const say = (data: unknown) =>
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data, source: win }));
    });
  return { ...r, say, replies };
}

/** happy-dom loads an `<iframe src>` over a real socket, and no `fetch` stub
 * sees it. Answered here, in the browser's own interceptor, so the frame gets
 * a window to speak through and nothing leaves the process. */
type HappyWindow = Window & {
  happyDOM: { settings: { fetch: { interceptor: unknown } } };
};

beforeEach(() => {
  (window as unknown as HappyWindow).happyDOM.settings.fetch.interceptor = {
    beforeAsyncRequest: async () => new Response("<html><body></body></html>", { headers: { "content-type": "text/html" } }),
  };
  served.base = BASE;
  vi.mocked(openServedWui).mockClear();
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  (window as unknown as HappyWindow).happyDOM.settings.fetch.interceptor = null;
  cleanup();
  vi.restoreAllMocks();
});

describe("a served WUI", () => {
  it("loads the page from its own address instead of assembling it", async () => {
    const { fs } = renderPane();

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`);
    expect(frame()!.hasAttribute("srcdoc")).toBe(false);
    // The boundary is unchanged: scripts, and NOT same-origin.
    expect(frame()!.getAttribute("sandbox")).toBe("allow-scripts");
    // Nothing is inlined — the browser fetches the siblings itself.
    expect(vi.mocked(fs.readFile).mock.calls.map((c) => c[0])).not.toContain("/sales/s.css");
  });

  it("is shown the single-page way where the deployment cannot serve it", async () => {
    served.base = null;
    renderPane();

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("srcdoc")).toContain("home");
    expect(frame()!.hasAttribute("src")).toBe(false);
  });

  it("follows a built page's entry into its folder", async () => {
    renderPane({
      files: { "/sales/site/index.html": "<html><body>docs</body></html>" },
      spec: { entry: "site/index.html" } as Partial<ViewSpec>,
    });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/site/index.html`);
  });

  it("still names a missing entry in plain language rather than loading a 404", async () => {
    renderPane({ files: {} });

    expect(await screen.findByRole("status")).toHaveTextContent("index.html");
    expect(frame()).toBeNull();
  });

  it("tells each page which part of the address is still the page, so a link out of it can be caught", async () => {
    const { say, replies } = await framed();

    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });

    expect(replies).toContainEqual({ proto: WUI_PROTOCOL, event: "scope", prefix: `${BASE}sales/` });
  });

  it("keeps the workspace on the sub-page it was on across a reload", async () => {
    const first = await framed();
    first.say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/#install` });
    first.unmount();

    renderPane();

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/setup/#install`);
  });

  it("does not reload the frame when a page announces itself", async () => {
    const { say } = await framed();
    const before = frame();

    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });

    expect(frame()).toBe(before);
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`);
  });

  it("ignores an announced address that is not this page's", async () => {
    const first = await framed();
    first.say({ proto: WUI_PROTOCOL, page: `${ORIGIN}/api/a/rca/items/x/files/secret` });
    first.unmount();

    renderPane();

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`);
  });

  it("reloads the page it is on when Refresh is pressed", async () => {
    const { say } = await framed();
    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });
    const before = frame();

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(frame()).not.toBe(before));
    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/setup/`));
  });
});

describe("asking again", () => {
  it("asks again on Refresh whether the page can be served — the last answer may have been a moment's", async () => {
    served.base = null;
    renderPane();
    await waitFor(() => expect(frame()?.hasAttribute("srcdoc")).toBe(true));

    served.base = BASE;
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(frame()?.getAttribute("src")).toBe(`${BASE}sales/index.html`));
  });

  it("asks again on a reader's Try again", async () => {
    served.base = null;
    renderPane({ files: {}, chrome: "viewer" });
    await screen.findByRole("button", { name: /try again/i });

    served.base = BASE;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(vi.mocked(openServedWui)).toHaveBeenCalledTimes(2));
  });
});

describe("a served WUI, deployed", () => {
  afterEach(() => vi.unstubAllGlobals());

  const stubDeploy = () =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: RequestInit) =>
        String(url).includes("/wui/deploy") && init?.method === "POST"
          ? new Response(
              JSON.stringify({
                slug: "rca",
                item_id: "item1",
                item_title: "Item one",
                path: "/sales/page.ai.yaml",
                title: "Sales",
                deployed_by: "u",
                deployed_at: 1,
                can_remove: true,
              }),
              { headers: { "content-type": "application/json" } },
            )
          : Promise.reject(new Error(`unexpected ${String(url)}`)),
      ),
    );

  it("shows the page Deploy verified when the page changed, though its address has not", async () => {
    /** The single-page way reloads because the document changed; a served page
     * has the same address before and after, so without a reload of its own
     * the frame would go on showing the page from before the Deploy. */
    stubDeploy();
    const files: Record<string, string> = { ...SITE };
    renderPane({ files });
    await waitFor(() => expect(frame()).toBeInTheDocument());
    const before = frame();

    files["/sales/index.html"] = "<html><head></head><body>home, rebuilt</body></html>";
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    await screen.findByRole("textbox", { name: /address/i });
    await waitFor(() => expect(frame()).not.toBe(before));
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`);
  });

  it("shows the page Deploy verified when only a stylesheet changed, and nobody announced it", async () => {
    /** The agent's own writes broadcast nothing (round 3), so a reload that
     * waited for a change event left the old page under "Deployed". */
    stubDeploy();
    const files: Record<string, string> = { ...SITE };
    renderPane({ files });
    await waitFor(() => expect(frame()).toBeInTheDocument());
    const before = frame();

    files["/sales/s.css"] = "body{color:blue}";
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    await screen.findByRole("textbox", { name: /address/i });
    await waitFor(() => expect(frame()).not.toBe(before));
  });

  it("reloads after a Deploy that built, though no file event arrived and the entry reads the same", async () => {
    setWuiAutoBuild(autoBuildScope("item1", "/sales"), false);
    let finish: () => void = () => {};
    const done = new Promise<void>((r) => (finish = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: RequestInit) => {
        const u = String(url);
        if (u.includes("/wui/build")) {
          const enc = new TextEncoder();
          const body = new ReadableStream({
            async start(c) {
              c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "output", text: "building\n" })}\n\n`));
              await done;
              c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "done", exit_code: 0 })}\n\n`));
              c.close();
            },
          });
          return new Response(body, { headers: { "content-type": "text/event-stream" } });
        }
        if (u.includes("/wui/deploy") && init?.method === "POST")
          return new Response(
            JSON.stringify({
              slug: "rca",
              item_id: "item1",
              item_title: "Item one",
              path: "/sales/page.ai.yaml",
              title: "Sales",
              deployed_by: "u",
              deployed_at: 1,
              can_remove: true,
            }),
            { headers: { "content-type": "application/json" } },
          );
        throw new Error(`unexpected ${u}`);
      }),
    );
    renderPane({ files: { ...SITE, "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }) } });
    await screen.findByRole("button", { name: /^rebuild$/i });

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/building/);
    await settle();
    // The frame as it stands WHILE the build runs: what must be replaced is
    // the page from before the build finished, whatever else moved meanwhile.
    const during = frame();
    finish();

    await screen.findByRole("textbox", { name: /address/i });
    await waitFor(() => expect(frame()).not.toBe(during));
  });

  it("reloads on every Deploy, on the sub-page the person was on — never a stale page, never back to the entry", async () => {
    /** Deploy is the author saying "ship this"; what it verified is what the
     * frame then shows. Telling an unchanged folder from a changed one took
     * three rounds and still missed writers that announce nothing, so it is
     * not attempted: the frame reloads, where it was. */
    stubDeploy();
    const { say } = await framed();
    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });
    const before = frame();

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    await screen.findByRole("textbox", { name: /address/i });
    await waitFor(() => expect(frame()).not.toBe(before));
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/setup/`);
  });
});

describe("a remembered sub-page that is gone", () => {
  it("drops it and opens the entry, when it is what the frame was opened on", async () => {
    sessionStorage.setItem("wui-page:item1:/sales/page.ai.yaml", "sales/gone/");
    const { say } = await framed();
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/gone/`);

    say({ proto: WUI_PROTOCOL, missing: `${BASE}sales/gone/` });

    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`));
    expect(sessionStorage.getItem("wui-page:item1:/sales/page.ai.yaml")).toBeNull();
  });

  it("ignores a 'gone' that names an address which is not this page's", async () => {
    sessionStorage.setItem("wui-page:item1:/sales/page.ai.yaml", "sales/setup/");
    const { say } = await framed();
    const before = frame();

    say({ proto: WUI_PROTOCOL, missing: `${ORIGIN}/api/a/rca/items/x/files/y` });

    await settle();
    expect(frame()).toBe(before);
    expect(sessionStorage.getItem("wui-page:item1:/sales/page.ai.yaml")).toBe("sales/setup/");
  });

  it("drops it from the reader's address too", async () => {
    window.history.replaceState(null, "", "/w/rca/item1/sales/page.ai.yaml?page=sales%2Fgone%2F&x=1");
    const { say } = await framed({ chrome: "viewer" });

    say({ proto: WUI_PROTOCOL, missing: `${BASE}sales/gone/` });

    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`));
    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBeNull();
    expect(params.get("x")).toBe("1");
  });

  it("treats the page a Refresh lands on as a fresh start, so a gone one is still dropped", async () => {
    const { say } = await framed();
    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/setup/`));
    // From the frame now on screen — the Refresh remounted it, and the pane
    // listens to that window only.
    const now = frame()!.contentWindow as Window;
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { proto: WUI_PROTOCOL, missing: `${BASE}sales/setup/` }, source: now }),
      );
    });

    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`));
  });

  it("leaves a broken link followed mid-visit alone — Back is the way out, and the last good page is kept", async () => {
    const { say } = await framed();
    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });
    const before = frame();

    say({ proto: WUI_PROTOCOL, missing: `${BASE}sales/broken/` });

    expect(frame()).toBe(before);
    expect(sessionStorage.getItem("wui-page:item1:/sales/page.ai.yaml")).toBe("sales/setup/");
  });
});

describe("two view files in one folder", () => {
  it("opens each on its own page, not the one the other was on", async () => {
    const files = { ...SITE, "/sales/alt.html": "<html><body>alt</body></html>" };
    const r = renderPane({ files });
    await waitFor(() => expect(frame()).toBeInTheDocument());
    const win = frame()!.contentWindow as Window;
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` }, source: win }));
    });

    r.showView({ path: "/sales/alt.ai.yaml", spec: { entry: "alt.html" } as Partial<ViewSpec> });
    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/alt.html`));

    // And back: the first one is where it was left.
    r.showView({ path: "/sales/page.ai.yaml", spec: {} });
    await waitFor(() => expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/setup/`));
  });
});

describe("a served WUI's reader page", () => {
  it("opens on the sub-page its address names", async () => {
    window.history.replaceState(null, "", "/w/rca/item1/sales/page.ai.yaml?page=sales%2Fsetup%2F");

    renderPane({ chrome: "viewer" });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/setup/`);
  });

  it("writes the page it lands on into the address, so the address can be shared", async () => {
    window.history.replaceState(null, "", "/w/rca/item1/sales/page.ai.yaml?x=1");
    const { say } = await framed({ chrome: "viewer" });

    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/#install` });

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("sales/setup/#install");
    expect(params.get("x")).toBe("1");
    expect(window.location.pathname).toBe("/w/rca/item1/sales/page.ai.yaml");
  });

  it("adds no history entry of its own — the frame's navigation already made one", async () => {
    window.history.replaceState(null, "", "/w/rca/item1/sales/page.ai.yaml");
    const { say } = await framed({ chrome: "viewer" });
    const depth = window.history.length;

    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/setup/` });
    say({ proto: WUI_PROTOCOL, page: `${BASE}sales/other/` });

    expect(window.history.length).toBe(depth);
  });

  it("does not remember the bare pass address as a page", async () => {
    window.history.replaceState(null, "", "/w/rca/item1/sales/page.ai.yaml");
    const { say } = await framed({ chrome: "viewer" });

    say({ proto: WUI_PROTOCOL, page: BASE });

    expect(new URLSearchParams(window.location.search).get("page")).toBeNull();
  });

  it.each([
    ["a climb out of the page", "..%2F..%2Fapi%2Fa%2Frca"],
    ["another site", "https%3A%2F%2Fevil.test%2F"],
    ["a protocol-relative address", "%2F%2Fevil.test%2F"],
  ])("refuses %s in the address and opens the entry instead", async (_why, page) => {
    window.history.replaceState(null, "", `/w/rca/item1/sales/page.ai.yaml?page=${page}`);

    renderPane({ chrome: "viewer" });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()!.getAttribute("src")).toBe(`${BASE}sales/index.html`);
  });
});

describe("leaving a WUI", () => {
  it("asks the reader before opening a link to another site, naming where it goes", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { say } = await framed();

    say({ proto: WUI_PROTOCOL, open: "https://example.com/x?y=1" });

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("example.com");
    expect(dialog).toHaveTextContent("https://example.com/x?y=1");
    expect(open).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    await waitFor(() => expect(open).toHaveBeenCalledWith("https://example.com/x?y=1", "_blank", "noopener,noreferrer"));
  });

  it("opens nothing when the reader cancels", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { say } = await framed();

    say({ proto: WUI_PROTOCOL, open: "https://example.com/" });
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(open).not.toHaveBeenCalled();
  });

  it("never offers an address that is not a place to visit", async () => {
    const { say } = await framed();

    say({ proto: WUI_PROTOCOL, open: "javascript:alert(1)" });

    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks once, not once per request, while a question is already up", async () => {
    const { say } = await framed();

    say({ proto: WUI_PROTOCOL, open: "https://a.test/" });
    say({ proto: WUI_PROTOCOL, open: "https://b.test/" });

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("a.test");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("asks before a mail link too — it is a place a person goes", async () => {
    const { say } = await framed();

    say({ proto: WUI_PROTOCOL, open: "mailto:lead@example.com" });

    expect(await screen.findByRole("dialog")).toHaveTextContent("lead@example.com");
  });

  it("tells a reader about one workspace file at a time", async () => {
    const { say } = await framed({ chrome: "viewer" });

    say({ proto: WUI_PROTOCOL, leave: "/a.md" });
    say({ proto: WUI_PROTOCOL, leave: "/b.md" });

    expect(await screen.findByRole("dialog")).toHaveTextContent("/a.md");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("opens a workspace file a link points at, in the workspace", async () => {
    const openFile = vi.fn();
    const { say } = await framed({ openFile });

    say({ proto: WUI_PROTOCOL, leave: "/notes.md" });

    expect(openFile).toHaveBeenCalledWith("/notes.md");
  });

  it("tells a reader that such a link points into the workspace", async () => {
    const { say } = await framed({ chrome: "viewer" });

    say({ proto: WUI_PROTOCOL, leave: "/notes.md" });

    expect(await screen.findByRole("dialog")).toHaveTextContent("/notes.md");
  });
});
