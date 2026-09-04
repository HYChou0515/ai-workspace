// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { autoBuildScope, getWuiAutoBuild, setWuiAutoBuild } from "../../lib/wuiAutoBuild";
import type { FileContent } from "../../api/types";
import { subscribeAgentDraft } from "../../lib/agentDraftBus";
import { publishFileChanged } from "../../lib/fileChangedBus";
import { QueryWrap } from "../../test/queryWrapper";
import type { ViewSpec } from "../entity/types";
import { WUI_CSP } from "./assemble";
import { WUI_PROTOCOL } from "./protocol";
import { MAX_REPORTS, WuiView } from "./WuiView";

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
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return text(path, files[path]);
    }),
    writeFile: vi.fn(async (path: string, body: string) => {
      files[path] = body;
    }),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

function renderWui(files: Record<string, string>, spec: Partial<ViewSpec> = {}) {
  return render(
    <QueryWrap>
      <FileServiceProvider value={svc(files)}>
        <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "", ...spec } as ViewSpec} />
      </FileServiceProvider>
    </QueryWrap>,
  );
}

const frame = () => document.querySelector("iframe");

/** Speak as the page inside the frame, and capture what comes back. */
async function withFrame(files: Record<string, string>) {
  renderWui(files);
  await waitFor(() => expect(frame()).toBeInTheDocument());
  const win = frame()?.contentWindow as Window;
  const replies: unknown[] = [];
  vi.spyOn(win, "postMessage").mockImplementation((m: unknown) => replies.push(m));
  const say = (data: unknown, source: unknown = win) =>
    window.dispatchEvent(new MessageEvent("message", { data, source: source as Window }));
  return { say, replies };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WuiView", () => {
  it("runs the folder's index.html in the frame", async () => {
    renderWui({ "/sales/index.html": "<html><body><h1>Yield</h1></body></html>" });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()?.getAttribute("srcdoc")).toContain("<h1>Yield</h1>");
  });

  it("grants scripts but NOT same-origin, which is the whole boundary", async () => {
    // With `allow-same-origin` the frame could read cookies, reach the parent
    // DOM and call our API as the signed-in user. Without it the origin is
    // `null` and postMessage is the only way out — so this attribute IS the
    // security model, not a detail of it.
    renderWui({ "/sales/index.html": "<html><body>hi</body></html>" });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()?.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("carries the CSP into the document it runs", async () => {
    renderWui({ "/sales/index.html": "<html><head></head><body>hi</body></html>" });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()?.getAttribute("srcdoc")).toContain(WUI_CSP);
  });

  it("inlines the folder's siblings, so the frame needs no network", async () => {
    renderWui({
      "/sales/index.html": `<html><head><link rel="stylesheet" href="s.css"></head><body><script src="./a.js"></script></body></html>`,
      "/sales/s.css": "body{color:red}",
      "/sales/a.js": "console.log(1)",
    });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    const doc = frame()?.getAttribute("srcdoc") ?? "";
    expect(doc).toContain("body{color:red}");
    expect(doc).toContain("console.log(1)");
  });

  it("honours an `entry` other than index.html", async () => {
    renderWui({ "/sales/main.html": "<html><body>main</body></html>" }, { entry: "main.html" } as Partial<ViewSpec>);

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(frame()?.getAttribute("srcdoc")).toContain("main");
  });

  it("names the missing file in plain language instead of rendering blank", async () => {
    // A blank pane is exactly where someone who cannot open a console gets
    // stuck; the message has to be the thing they can act on or forward.
    renderWui({});

    expect(await screen.findByRole("status")).toHaveTextContent("index.html");
    expect(frame()).toBeNull();
  });

  it("answers a bridge request from its own frame", async () => {
    const { say, replies } = await withFrame({
      "/sales/index.html": "<html><body>hi</body></html>",
      "/notes.md": "the notes",
    });

    say({ proto: WUI_PROTOCOL, id: "7", verb: "readFile", args: { path: "/notes.md" } });

    await waitFor(() => expect(replies).toHaveLength(1));
    expect(replies[0]).toMatchObject({ id: "7", ok: true, value: { text: "the notes" } });
  });

  it("ignores a message from any other window", async () => {
    // The page shares `window` with the rest of the app and with anything else
    // the browser lets talk to it; only OUR frame is the page.
    const { say, replies } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    say({ proto: WUI_PROTOCOL, id: "7", verb: "whoami" }, { postMessage: vi.fn() });

    await new Promise((r) => setTimeout(r, 0));
    expect(replies).toHaveLength(0);
  });

  it("ignores a message that is not ours", async () => {
    const { say, replies } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    say({ type: "webpack-hmr" });

    await new Promise((r) => setTimeout(r, 0));
    expect(replies).toHaveLength(0);
  });

  it("tells the page when someone else edits a file", async () => {
    // Not a reload: the page is holding state we cannot merge, so it hears what
    // changed and decides. Without this an editor silently overwrites.
    const { replies } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    publishFileChanged("item1", "/sales/data.json");

    await waitFor(() => expect(replies).toHaveLength(1));
    expect(replies[0]).toMatchObject({ event: "file_changed", path: "/sales/data.json" });
  });

  it("refuses a tool the view file did not declare", async () => {
    // End of the wiring: the declaration is read off the view file and reaches
    // the gate. Nothing here touches the network, because nothing should.
    renderWui({ "/sales/index.html": "<html><body>hi</body></html>" }, {
      tools: ["lot-status"],
    } as Partial<ViewSpec>);
    await waitFor(() => expect(frame()).toBeInTheDocument());
    const win = frame()?.contentWindow as Window;
    const replies: unknown[] = [];
    vi.spyOn(win, "postMessage").mockImplementation((m: unknown) => replies.push(m));

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { proto: WUI_PROTOCOL, id: "9", verb: "callTool", args: { name: "other" } },
        source: win,
      }),
    );

    await waitFor(() => expect(replies).toHaveLength(1));
    expect(replies[0]).toMatchObject({ ok: false });
    expect((replies[0] as { error: string }).error).toContain("other");
  });

  it("shows what the page reported, because nobody here can open a console", async () => {
    const { say } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    say({ proto: WUI_PROTOCOL, report: "error", message: "x is not a function (app.js:12)" });

    expect(await screen.findByText(/x is not a function/)).toBeInTheDocument();
  });

  it("asks the page to enter pick mode when Report is pressed", async () => {
    // The parent cannot reach into a null-origin frame, so pointing at
    // something can only happen inside — this is the request to start.
    const { replies } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    fireEvent.click(screen.getByRole("button", { name: /report/i }));

    expect(replies.at(-1)).toMatchObject({ command: "pick", on: true });
  });

  it("hands the report to the chat box instead of asking the user to retype it", async () => {
    const offered: string[] = [];
    const off = subscribeAgentDraft("item1", (t) => offered.push(t));
    const { say } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    say({
      proto: WUI_PROTOCOL,
      report: "pick",
      message: "pointed",
      detail: { html: "<b>42</b>", marker: "total", styles: { display: "flex" } },
    });
    fireEvent.click(await screen.findByRole("button", { name: /tell the agent/i }));

    expect(offered).toHaveLength(1);
    expect(offered[0]).toContain("total");
    expect(offered[0]).toContain("display: flex");
    expect(offered[0]).toContain("/sales");
    off();
  });

  it("clears the reports once they have been handed over", async () => {
    const off = subscribeAgentDraft("item1", () => {});
    const { say } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    say({ proto: WUI_PROTOCOL, report: "error", message: "boom" });
    fireEvent.click(await screen.findByRole("button", { name: /tell the agent/i }));

    await waitFor(() => expect(screen.queryByText(/boom/)).not.toBeInTheDocument());
    off();
  });

  it("answers a refusal when the write is rejected, instead of never answering", async () => {
    // The file verbs used to `await` unguarded, so a 403 (read-only viewer) or a
    // 507 (full workspace) rejected the dispatch, posted nothing, and left the
    // page's `await workspace.writeFile(...)` pending forever — a save button
    // that does nothing, with no message, which is the exact opposite of this
    // bridge's rule that a refusal is a sentence.
    const files: Record<string, string> = { "/sales/index.html": "<html><body>hi</body></html>" };
    const service = svc(files);
    (service.writeFile as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("this workspace is full"),
    );
    render(
      <QueryWrap>
        <FileServiceProvider value={service}>
          <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
        </FileServiceProvider>
      </QueryWrap>,
    );
    await waitFor(() => expect(frame()).toBeInTheDocument());
    const win = frame()?.contentWindow as Window;
    const replies: unknown[] = [];
    vi.spyOn(win, "postMessage").mockImplementation((m: unknown) => replies.push(m));

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { proto: WUI_PROTOCOL, id: "1", verb: "writeFile", args: { path: "a.json", text: "x" } },
        source: win,
      }),
    );

    await waitFor(() => expect(replies).toHaveLength(1));
    expect(replies[0]).toMatchObject({ id: "1", ok: false });
    expect((replies[0] as { error: string }).error).toContain("this workspace is full");
  });

  it("keeps a page stuck in an error loop from freezing the workspace", async () => {
    // The runtime reports every uncaught error. A page throwing inside a timer
    // produces one message per frame, and an unbounded list re-rendered per
    // message locks up the WHOLE app — including the button that would clear it.
    const { say } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    for (let i = 0; i < MAX_REPORTS + 20; i++) {
      say({ proto: WUI_PROTOCOL, report: "error", message: `boom ${i}` });
    }

    await waitFor(() =>
      expect(screen.getByRole("log", { name: "Reports" }).children.length).toBe(MAX_REPORTS),
    );
    // BOTH ends survive. The first error is usually the cause, so keeping only
    // the newest throws away the thing worth forwarding; keeping only the
    // oldest hides what the person is looking at now.
    expect(screen.getByText(/boom 0$/)).toBeInTheDocument();
    expect(screen.getByText(/boom 119$/)).toBeInTheDocument();
    // And the gap is stated: a truncated transcript that reads as complete tells
    // the agent what happened and does not tell it that more did.
    // And the gap is stated, with the CUMULATIVE and CORRECT count. 120 pushed,
    // 100 slots of which one holds the marker itself → 21 real reports lost.
    // Both halves have been wrong here: a marker counting only the last trim
    // said "1", and one that forgot its own slot said "20".
    expect(screen.getByText(/and 21 more reports in between, dropped/)).toBeInTheDocument();
    expect(screen.getAllByText(/boom /).length).toBe(MAX_REPORTS - 1);
  });

  it("does not tell the page about its own save", async () => {
    // Found by running one in a browser: every save came back as "somebody else
    // changed this", which discredits the warning that matters.
    const { say, replies } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" });

    say({ proto: WUI_PROTOCOL, id: "1", verb: "writeFile", args: { path: "data.json", text: "[]" } });
    await waitFor(() => expect(replies).toHaveLength(1));
    publishFileChanged("item1", "/sales/data.json");

    await new Promise((r) => setTimeout(r, 0));
    expect(replies).toHaveLength(1); // the write's own reply, and no echo

    // A second, unmatched event is somebody else and still gets through.
    publishFileChanged("item1", "/sales/data.json");
    await waitFor(() => expect(replies).toHaveLength(2));
    expect(replies[1]).toMatchObject({ event: "file_changed" });
  });

  it("rebuilds the page on Refresh, since nothing reloads it automatically", async () => {
    const files: Record<string, string> = { "/sales/index.html": "<html><body>v1</body></html>" };
    render(
      <QueryWrap>
        <FileServiceProvider value={svc(files)}>
          <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
        </FileServiceProvider>
      </QueryWrap>,
    );

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v1"));
    files["/sales/index.html"] = "<html><body>v2</body></html>";
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v2"));
  });
});

describe("WuiView: rebuilding a page that has a build step", () => {
  const PAGE = { "/sales/index.html": "<html><body>v1</body></html>" };
  const BUILT = {
    ...PAGE,
    "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
  };

  /** Render inside a workspace, which is what gives the pane an item to build
   * in — the slug is how every workspace route is addressed. */
  function renderIn(files: Record<string, string>, path = "/sales/page.ai.yaml") {
    return render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path={path} spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
  }

  /** A build whose output arrives in pieces, with a gate between them. */
  function serveBuild(chunks: string[]) {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const encode = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            async start(controller) {
              controller.enqueue(encode.encode(chunks[0]));
              await gate;
              for (const chunk of chunks.slice(1)) controller.enqueue(encode.encode(chunk));
              controller.close();
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }),
    );
    return { release };
  }

  const sse = (payload: unknown) => `data: ${JSON.stringify(payload)}\n\n`;

  // These are the MANUAL path. Rebuilding on open is on by default — that is
  // what makes a stale page impossible — so a test about the button has to say
  // it is not testing the automatic one.
  beforeEach(() => setWuiAutoBuild(autoBuildScope("item1", "/sales"), false));
  afterEach(() => localStorage.clear());
  // The label says "Building…" while it runs — that IS the progress signal, so
  // the helper has to accept both.
  const rebuild = () => screen.getByRole("button", { name: /rebuild|building/i });
  /** A build whose output arrives in pieces, with a gate before the last one. */
  function serveGated(chunks: string[]) {
    let go: () => void = () => {};
    const gate = new Promise<void>((r) => (go = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const encode = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            async start(c) {
              for (const chunk of chunks.slice(0, -1)) c.enqueue(encode.encode(chunk));
              await gate;
              c.enqueue(encode.encode(chunks[chunks.length - 1]));
              c.close();
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }),
    );
    return { release: () => go() };
  }

  afterEach(() => vi.unstubAllGlobals());

  it("offers no Rebuild on a page that has no build", async () => {
    // Most pages are plain files and nothing to build. A button that ran a
    // build that cannot exist would fail, loudly, for a page that is fine.
    renderIn(PAGE);

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /rebuild/i })).not.toBeInTheDocument();
  });

  it("offers Rebuild once the folder declares the script the platform runs", async () => {
    renderIn(BUILT);

    expect(await screen.findByRole("button", { name: /rebuild/i })).toBeInTheDocument();
  });

  it("does not offer a rebuild at the workspace root, which the server refuses", async () => {
    // A root-level page has no folder to build in, and the route says so with a
    // 400. Offering the button anyway makes the platform look broken.
    renderIn({ "/index.html": "<html><body>v1</body></html>", "/package.json": BUILT["/sales/package.json"] }, "/page.ai.yaml");

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /rebuild/i })).not.toBeInTheDocument();
  });

  it("shows the build's output while it is still running", async () => {
    // The reason this exists. A build takes tens of seconds and fails often
    // while someone is iterating; a spinner cannot tell them apart from a hang,
    // and the compiler's own words are the whole value.
    const { release } = serveBuild([
      sse({ type: "output", text: "> vite build" }),
      sse({ type: "output", text: "built in 615ms" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn(BUILT);
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));

    expect(await screen.findByText(/vite build/)).toBeInTheDocument();
    // Still running: the last line has not been sent, and it is already on screen.
    expect(screen.queryByText(/615ms/)).not.toBeInTheDocument();

    release();
    // The log folds away when the build succeeds, so this opens it again — the
    // point of the test is that the output ARRIVED, not where it sits after.
    fireEvent.click(await screen.findByRole("button", { name: /show build output/i }));
    expect(await screen.findByText(/615ms/)).toBeInTheDocument();
  });

  it("shows the rebuilt page once the build succeeds", async () => {
    // Without this the person presses Rebuild, watches it succeed, and goes on
    // looking at the old page — the exact confusion the feature is here to end.
    const files = { ...BUILT };
    const { release } = serveBuild([sse({ type: "output", text: "ok" }), sse({ type: "done", exit_code: 0 })]);
    renderIn(files);

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v1"));
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/ok/);
    files["/sales/index.html"] = "<html><body>v2</body></html>";
    release();

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v2"));
  });

  it("leaves a failed build's own words on screen, and the page alone", async () => {
    const files = { ...BUILT };
    const { release } = serveBuild([
      sse({ type: "output", text: "src/main.jsx:12 Unexpected token" }),
      sse({ type: "done", exit_code: 1 }),
    ]);
    renderIn(files);

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v1"));
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    files["/sales/index.html"] = "<html><body>v2</body></html>";
    release();

    expect(await screen.findByText(/Unexpected token/)).toBeInTheDocument();
    expect(await screen.findByText(/failed/i)).toBeInTheDocument();
    // A failed build produced no new `dist/`. Swapping the page here would
    // replace what someone is looking at with the SAME thing and call it a
    // rebuild.
    expect(frame()?.getAttribute("srcdoc")).toContain("v1");
  });

  it("says why when the build could not be started at all", async () => {
    // A refusal (403 for a viewer without `execute`, 400 for a bad folder)
    // arrives as an HTTP status, not as build output. Unshown, the button looks
    // like it did nothing.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "You cannot run things here." }), { status: 403 })),
    );
    renderIn(BUILT);

    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));

    expect(await screen.findByText(/cannot run things here/)).toBeInTheDocument();
  });

  it("puts a finished build's log away, because the page is the answer", async () => {
    // The log earns the top of the pane while it is running and for as long as
    // something went wrong. A build that succeeded has already said everything
    // it has to say — leaving twelve lines of vite output above somebody's page
    // is taking their pane for a receipt.
    const { release } = serveGated([
      sse({ type: "output", text: "vite v6.4.3 building for production..." }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));

    // Running: the output is the progress, so it is on screen.
    expect(await screen.findByText(/building for production/)).toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByText(/building for production/)).not.toBeInTheDocument());
    // Not gone without trace: one line says what happened, and opens it again.
    expect(screen.getByText(/Build finished/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show build output/i }));
    expect(await screen.findByText(/building for production/)).toBeInTheDocument();
  });

  it("unfolds again for the next build", async () => {
    // The fold is about a build that is OVER. Pressing Rebuild is asking to
    // watch one, and finding the log still folded would read as the button
    // doing nothing.
    const first = serveGated([
      sse({ type: "output", text: "first pass" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    first.release();
    await screen.findByText(/Build finished/);
    expect(screen.queryByText(/first pass/)).not.toBeInTheDocument();

    const { release } = serveGated([
      sse({ type: "output", text: "second pass" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: /rebuild/i }));

    expect(await screen.findByText(/second pass/)).toBeInTheDocument();
    release();
  });

  it("leaves a failed build's log open, which is the whole reason to look", async () => {
    const { release } = serveGated([
      sse({ type: "output", text: "src/main.jsx:12 Unexpected token" }),
      sse({ type: "done", exit_code: 1 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/Unexpected token/);

    release();
    await screen.findByText(/failed/i);

    expect(screen.getByText(/Unexpected token/)).toBeInTheDocument();
  });

  it("cannot be started twice over", async () => {
    // Two builds in one folder race over `dist/`, and the second one's output
    // interleaves with the first's in the log.
    const { release } = serveBuild([sse({ type: "output", text: "working" }), sse({ type: "done", exit_code: 0 })]);
    renderIn(BUILT);
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/working/);

    expect(rebuild()).toBeDisabled();
    fireEvent.click(rebuild());

    // Count the BUILD calls, not every fetch: the pane also asks who you are,
    // and a total that mixes the two agrees with the bug it is meant to catch.
    const builds = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).includes("/wui/build"));
    expect(builds).toHaveLength(1);
    release();
  });
});

describe("WuiView: rebuilding a page when it is opened", () => {
  const BUILT = {
    "/sales/index.html": "<html><body>v1</body></html>",
    "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
  };
  const PLAIN = { "/sales/index.html": "<html><body>v1</body></html>" };

  function renderIn(files: Record<string, string>) {
    return render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
  }

  const sse = (payload: unknown) => `data: ${JSON.stringify(payload)}\n\n`;

  /** A build that answers immediately — the automatic path is not driven by a
   * click, so there is nothing to hold open. */
  function serveBuild(body: string, status = 200) {
    const spy = vi.fn(
      async () =>
        new Response(body, {
          status,
          headers: { "content-type": status === 200 ? "text/event-stream" : "application/json" },
        }),
    );
    vi.stubGlobal("fetch", spy);
    return spy;
  }

  const buildCalls = () =>
    vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/wui/build"));

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("rebuilds a built page as soon as it is opened", async () => {
    // The setting under which a stale page is IMPOSSIBLE rather than unlikely,
    // and therefore the default. Nobody has to remember anything.
    const files = { ...BUILT };
    serveBuild(sse({ type: "output", text: "> vite build" }) + sse({ type: "done", exit_code: 0 }));
    renderIn(files);

    expect(await screen.findByText(/Build finished/)).toBeInTheDocument();
  });

  it("builds ONCE, though its own success re-reads the folder", async () => {
    // The success bumps the generation so the new `dist/` is what you see —
    // which re-runs the very effect that started the build. Unguarded this is
    // not a double build, it is an endless one.
    const files = { ...BUILT };
    serveBuild(sse({ type: "output", text: "built" }) + sse({ type: "done", exit_code: 0 }));
    renderIn(files);

    await screen.findByText(/Build finished/);
    await new Promise((r) => setTimeout(r, 50));
    expect(buildCalls()).toHaveLength(1);
  });

  it("shows the freshly built page, not the one that was there", async () => {
    const files = { ...BUILT };
    serveBuild(sse({ type: "done", exit_code: 0 }));
    files["/sales/index.html"] = "<html><body>v2</body></html>";
    renderIn(files);

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v2"));
  });

  it("does nothing on a page that has no build", async () => {
    // Most pages are plain files. Opening one must not run anything at all.
    serveBuild(sse({ type: "done", exit_code: 0 }));
    renderIn({ ...PLAIN });

    await waitFor(() => expect(frame()).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 50));
    expect(buildCalls()).toHaveLength(0);
  });

  it("does nothing when this page's viewer has turned it off", async () => {
    setWuiAutoBuild(autoBuildScope("item1", "/sales"), false);
    serveBuild(sse({ type: "done", exit_code: 0 }));
    renderIn({ ...BUILT });

    await screen.findByRole("button", { name: /rebuild/i });
    await new Promise((r) => setTimeout(r, 50));
    expect(buildCalls()).toHaveLength(0);
  });

  it("turns itself off for a viewer who is not allowed to run things", async () => {
    // A reader may open the item and not run anything in it. Left on, this
    // would greet them with the same refusal on every single open — so it stops
    // asking, and says why.
    serveBuild(JSON.stringify({ detail: "You cannot run things here." }), 403);
    renderIn({ ...BUILT });

    expect(await screen.findByText(/cannot run things here/)).toBeInTheDocument();
    expect(await screen.findByText(/turned off/i)).toBeInTheDocument();
    expect(getWuiAutoBuild(autoBuildScope("item1", "/sales"))).toBe(false);
  });

  it("keeps asking after a failure that is not a refusal", async () => {
    // A gateway hiccup is not permission. Disabling on any failure would let one
    // bad moment silently switch the feature off for good.
    serveBuild("<html>gateway</html>", 502);
    renderIn({ ...BUILT });

    await screen.findByText(/502/);
    expect(getWuiAutoBuild(autoBuildScope("item1", "/sales"))).toBe(true);
  });

  it("does not start a build when the setting is merely toggled", async () => {
    // "Rebuild when I open this" is a promise about OPENING. React re-runs the
    // effect whenever the preference changes — and twice on mount, under
    // StrictMode — and none of those is somebody opening the page.
    setWuiAutoBuild(autoBuildScope("item1", "/sales"), false);
    serveBuild(sse({ type: "done", exit_code: 0 }));
    renderIn({ ...BUILT });
    const toggle = await screen.findByLabelText(/rebuild when i open this/i);

    fireEvent.click(toggle); // on
    await new Promise((r) => setTimeout(r, 50));

    expect(getWuiAutoBuild(autoBuildScope("item1", "/sales"))).toBe(true);
    expect(buildCalls()).toHaveLength(0);
  });

  it("builds once, not twice, when React runs the effect twice", async () => {
    // StrictMode double-invokes every effect on mount, and the app runs in it.
    // Two builds in one folder race over `dist/` and interleave in the log.
    serveBuild(sse({ type: "output", text: "built" }) + sse({ type: "done", exit_code: 0 }));
    render(
      <StrictMode>
        <QueryWrap>
          <WorkspaceSlugProvider value="rca">
            <FileServiceProvider value={svc({ ...BUILT })}>
              <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
            </FileServiceProvider>
          </WorkspaceSlugProvider>
        </QueryWrap>
      </StrictMode>,
    );

    await screen.findByText(/Build finished/);
    await new Promise((r) => setTimeout(r, 50));
    expect(buildCalls()).toHaveLength(1);
  });

  it("does not shout about a missing dist/ while it is building one", async () => {
    // The first open of a page nobody has built yet: `dist/` really is absent,
    // and the pane said so in red — under a log showing the build that was
    // about to create it. Seen in a screenshot; alarming and, seconds later,
    // untrue.
    const { release } = (() => {
      let go: () => void = () => {};
      const gate = new Promise<void>((r) => (go = r));
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          const encode = new TextEncoder();
          return new Response(
            new ReadableStream<Uint8Array>({
              async start(c) {
                c.enqueue(encode.encode(sse({ type: "output", text: "vite build" })));
                await gate;
                c.enqueue(encode.encode(sse({ type: "done", exit_code: 0 })));
                c.close();
              },
            }),
            { status: 200, headers: { "content-type": "text/event-stream" } },
          );
        }),
      );
      return { release: () => go() };
    })();
    // No entry file at all — exactly the state before a first build.
    const files: Record<string, string> = {
      "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
    };
    renderIn(files);

    await screen.findByText(/vite build/);
    expect(screen.queryByText(/no index\.html/i)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/building/i);

    // And once it is built, the page appears.
    files["/sales/index.html"] = "<html><body>v1</body></html>";
    release();
    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v1"));
  });

  it("shows the build's words without its colour codes", async () => {
    serveBuild(
      sse({ type: "output", text: "\u001b[32m✓\u001b[39m built in 565ms" }) +
        sse({ type: "done", exit_code: 0 }),
    );
    renderIn({ ...BUILT });

    // Folded away on success, so open it: this is about what the output SAYS.
    fireEvent.click(await screen.findByRole("button", { name: /show build output/i }));
    const log = await screen.findByRole("log", { name: "Build output" });
    await waitFor(() => expect(log).toHaveTextContent(/built in 565ms/));
    expect(log.textContent).not.toContain("[32m");
  });

  it("does not show a page it is about to replace", async () => {
    // Found by recording a demo. Opening a page and building it at the same
    // time makes the page's own reads race the sandbox restore the build
    // triggers: `app.js` and `style.css` come back missing for a moment, so the
    // frame renders unstyled and inert, with three red lines under a log that
    // is still working. Seconds later the build finishes and it all comes
    // right — which is exactly why nobody should be shown the first version.
    const { release } = (() => {
      let go: () => void = () => {};
      const gate = new Promise<void>((r) => (go = r));
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          const encode = new TextEncoder();
          return new Response(
            new ReadableStream<Uint8Array>({
              async start(c) {
                c.enqueue(encode.encode(sse({ type: "output", text: "npm pack chart.js" })));
                await gate;
                c.enqueue(encode.encode(sse({ type: "done", exit_code: 0 })));
                c.close();
              },
            }),
            { status: 200, headers: { "content-type": "text/event-stream" } },
          );
        }),
      );
      return { release: () => go() };
    })();
    // The document reads perfectly well — this is not the missing-dist case.
    renderIn({ ...BUILT });

    await screen.findByText(/npm pack/);
    expect(document.querySelector("iframe")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/building/i);

    release();
    await waitFor(() => expect(document.querySelector("iframe")).toBeInTheDocument());
  });

  it("keeps the toolbar short and explains on hover", async () => {
    // The pane is a strip above somebody's page; a sentence in it is a sentence
    // taken from the page. The short form stays readable next to Rebuild
    // ("Rebuild … on open") and the whole explanation is one hover away.
    setWuiAutoBuild(autoBuildScope("item1", "/sales"), false);
    serveBuild(sse({ type: "done", exit_code: 0 }));
    renderIn({ ...BUILT });

    const toggle = await screen.findByLabelText(/rebuild when i open this/i);
    expect(toggle.closest("label")).toHaveAttribute(
      "title",
      expect.stringMatching(/rebuild this page whenever you open it/i),
    );
    expect(toggle.closest("label")).toHaveTextContent(/^on open$/);
  });

  it("lets the viewer turn it off from the pane", async () => {
    // The user's own choice, in front of them — not a hidden default.
    serveBuild(sse({ type: "done", exit_code: 0 }));
    renderIn({ ...BUILT });

    const toggle = await screen.findByLabelText(/rebuild when i open this/i);
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    expect(getWuiAutoBuild(autoBuildScope("item1", "/sales"))).toBe(false);
  });
});
