// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import { HttpError } from "../../api/http";
import { qk } from "../../api/queryKeys";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { autoBuildScope, getWuiAutoBuild, setWuiAutoBuild } from "../../lib/wuiAutoBuild";
import type { FileContent } from "../../api/types";
import { subscribeAgentDraft } from "../../lib/agentDraftBus";
import { publishFileChanged } from "../../lib/fileChangedBus";
import type { QueryClient } from "@tanstack/react-query";
import { makeTestQueryClient, QueryWrap } from "../../test/queryWrapper";
import type { ViewSpec } from "../entity/types";
import { WUI_CSP } from "./assemble";
import { WUI_PROTOCOL } from "./protocol";
import { MAX_REPORTS, WuiView, type WuiChrome } from "./WuiView";

const text = (path: string, body: string): FileContent => ({
  kind: "text",
  path,
  size: body.length,
  text: body,
  encoding: "utf-8",
});

/** What the real service throws for a file that is not there — an
 * `HttpError(404)`, the one shape `classifyReadFailure` treats as certain
 * absence. A plain `Error` reached "missing" through its lenient catch-all
 * instead, so no test here exercised the 404 branch production takes. */
const notFound = (path: string) => new HttpError(404, `read ${path} failed: 404`);

function svc(files: Record<string, string>): FileService {
  return {
    scopeId: "item1",
    caps: { write: true, delete: true },
    readFile: vi.fn(async (path: string) => {
      if (!(path in files)) throw notFound(path);
      return text(path, files[path]);
    }),
    writeFile: vi.fn(async (path: string, body: string) => {
      files[path] = body;
    }),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

function renderWui(files: Record<string, string>, spec: Partial<ViewSpec> = {}, chrome?: WuiChrome) {
  return render(
    <QueryWrap>
      <FileServiceProvider value={svc(files)}>
        <WuiView
          path="/sales/page.ai.yaml"
          spec={{ view: "wui", entity: "", ...spec } as ViewSpec}
          chrome={chrome}
        />
      </FileServiceProvider>
    </QueryWrap>,
  );
}

const frame = () => document.querySelector("iframe");

/** Speak as the page inside the frame, and capture what comes back. */
async function withFrame(files: Record<string, string>, chrome?: WuiChrome) {
  renderWui(files, {}, chrome);
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

  it("keeps the page's reports from a reader, who has nobody to hand them to", async () => {
    /**
     * The test above is the positive control: the same report, in workspace
     * chrome, is shown. The reports exist to be handed to the agent ("Tell the
     * agent"), and a reader has no agent — a pane they can only stare at is
     * the toolbar leak in another coat (docs/plan-wui-deploy.md, P1).
     */
    const { say, replies } = await withFrame({ "/sales/index.html": "<html><body>hi</body></html>" }, "viewer");

    say({ proto: WUI_PROTOCOL, report: "error", message: "x is not a function (app.js:12)" });
    // The message was received (the bridge is alive for a reader) …
    say({ proto: WUI_PROTOCOL, id: "1", verb: "whoami" });
    await waitFor(() => expect(replies).toHaveLength(1));

    // … but nothing was drawn for it.
    expect(screen.queryByRole("log", { name: /reports/i })).toBeNull();
    expect(screen.queryByText(/x is not a function/)).toBeNull();
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

  it("does not even look for a manifest at the workspace root", async () => {
    // `canBuild` is false there whatever the answer, so the read is a 404
    // nobody can use — once per session, in everyone's console.
    const service = svc({ "/index.html": "<html><body>v1</body></html>" });
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={service}>
            <WuiView path="/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );

    await waitFor(() => expect(frame()).toBeInTheDocument());
    const asked = vi.mocked(service.readFile).mock.calls.map(([path]) => path);
    expect(asked).not.toContain("/package.json");
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

  it("keeps a failed build's log when Refresh is pressed", async () => {
    // Refresh after a failure is the reflex — you fixed the file, now show me.
    // It used to clear the log, taking the compiler error, the only explanation
    // on screen, with it, while the same unchanged page re-rendered.
    const { release } = serveGated([
      sse({ type: "output", text: "src/main.jsx:12 Unexpected token" }),
      sse({ type: "done", exit_code: 1 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();
    await screen.findByText(/failed/i);

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    expect(screen.getByText(/Unexpected token/)).toBeInTheDocument();
  });

  it("cleans the output as a whole, not chunk by chunk", async () => {
    // An escape sequence is a byte fragment too: split across two chunks, a
    // per-chunk clean leaves its tail as literal text — the exact artefact it
    // exists to remove.
    const esc = "\u001b[32m";
    const { release } = serveGated([
      sse({ type: "output", text: esc.slice(0, 2) }),
      sse({ type: "output", text: esc.slice(2) + "built in 581ms" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();

    fireEvent.click(await screen.findByRole("button", { name: /show build output/i }));
    const log = await screen.findByRole("log", { name: "Build output" });
    await waitFor(() => expect(log).toHaveTextContent(/built in 581ms/));
    expect(log.textContent).not.toContain("[32m");
  });

  it("announces the verdict, since the log itself is silent", async () => {
    // The log is `aria-live="off"` on purpose — it emits a chunk every few
    // milliseconds. That only works if the ONE line that carries the outcome is
    // announced instead; otherwise a screen reader is told nothing at all about
    // a build it watched start.
    const { release } = serveGated([
      sse({ type: "output", text: "vite build" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();

    const summary = await screen.findByText(/Build finished/);
    expect(summary).toHaveAttribute("aria-live", "polite");
  });

  it("does not narrate the whole build to a screen reader", async () => {
    // Two polite live regions in one pane: the reports panel, which speaks
    // rarely and matters, and a build log that emits a chunk every few
    // milliseconds. Announcing every chunk drowns the one that matters.
    const { release } = serveGated([
      sse({ type: "output", text: "transforming..." }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();

    fireEvent.click(await screen.findByRole("button", { name: /show build output/i }));
    const log = await screen.findByRole("log", { name: "Build output" });
    expect(log).toHaveAttribute("aria-live", "off");
  });

  it("does not open a gap when the build's last line ended in a carriage return", async () => {
    // Regression from moving the cleaning to render time. The glue check reads
    // the log as STORED, and stored used to mean cleaned — where `\r` had
    // already become `\n`. Reading raw text, a progress line ending in `\r`
    // looks unterminated, so our own line gets an extra break before it.
    //
    // It does not show, because the cleaning collapses the `\r\n` that
    // results. So this pins the CLEANING half only: remove the glue and it
    // still passes, which is why the glue has its own test above.
    const { release } = serveGated([
      sse({ type: "output", text: "Progress: resolved 115\r" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();

    fireEvent.click(await screen.findByRole("button", { name: /show build output/i }));
    const log = await screen.findByRole("log", { name: "Build output" });
    await waitFor(() => expect(log).toHaveTextContent(/Build finished/));
    expect(log.textContent).toBe("Progress: resolved 115\nBuild finished.\n");
  });

  it("does not carry one page's build log onto another page", async () => {
    // The log used to be cleared by Refresh, which hid this: nothing resets the
    // build state when the pane moves to a different folder without
    // unmounting, so the previous page's build — its log, and the fact that it
    // has already auto-built — belonged to the new one.
    const files = {
      "/sales/index.html": "<html><body>sales</body></html>",
      "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
      "/costs/index.html": "<html><body>costs</body></html>",
    };
    const { release } = serveGated([
      sse({ type: "output", text: "vite building sales" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    const view = render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();
    await screen.findByText(/Build finished/);

    view.rerender(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path="/costs/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );

    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("costs"));
    expect(screen.queryByText(/Build finished/)).not.toBeInTheDocument();
  });

  it("lets a build finish into a page that has already been left", async () => {
    // The pane can move to another page without unmounting, and the build it
    // started keeps running. Everything it does on the way out — the log, the
    // verdict, the re-read that swaps the frame — would land on a page that
    // never asked for it.
    const files = {
      "/sales/index.html": "<html><body>sales</body></html>",
      "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
      "/costs/index.html": "<html><body>costs</body></html>",
    };
    const { release } = serveGated([
      sse({ type: "output", text: "vite building sales" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    const at = (path: string) => (
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path={path} spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>
    );

    const view = render(at("/sales/page.ai.yaml"));
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/vite building sales/);

    // Leave while it is still running, then let it finish.
    view.rerender(at("/costs/page.ai.yaml"));
    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("costs"));
    release();
    await new Promise((r) => setTimeout(r, 60));

    expect(screen.queryByText(/vite building sales/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Build finished/)).not.toBeInTheDocument();
    expect(frame()?.getAttribute("srcdoc")).toContain("costs");
    // And the build it ran was the one it was asked for, not whatever is on
    // screen when the request is made.
    const [url, init] = vi
      .mocked(fetch)
      .mock.calls.find(([u]) => String(u).includes("/wui/build")) as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toContain("/wui/build");
    expect(JSON.parse(String(init.body))).toEqual({ folder: "/sales" });
  });

  it("does not let the build you left finish the one you are watching", async () => {
    // The page you left keeps building; the page you arrived at starts its own.
    // When the first one ends, its `finally` used to clear `building` and
    // `firstBuild` unconditionally — so the pane declared the SECOND build over
    // while it was still running: Rebuild enabled again, and the page shown
    // before the build that was going to replace it had finished.
    const files = {
      "/sales/index.html": "<html><body>sales</body></html>",
      "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
      "/costs/index.html": "<html><body>costs</body></html>",
      "/costs/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
    };

    // Two gates: the first build is released only after the second has begun.
    const gates: (() => void)[] = [];
    let served = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        // Only the build route: the pane also asks who you are, and a stub that
        // answers everything gave that request the first gate and hung it.
        if (!String(url).includes("/wui/build")) return new Response("{}", { status: 404 });
        const nth = served++;
        const encode = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            async start(c) {
              c.enqueue(encode.encode(sse({ type: "output", text: `build ${nth}` })));
              await new Promise<void>((r) => gates.push(r));
              c.enqueue(encode.encode(sse({ type: "done", exit_code: 0 })));
              c.close();
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }),
    );

    const at = (path: string) => (
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path={path} spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>
    );

    const view = render(at("/sales/page.ai.yaml"));
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/build 0/);

    view.rerender(at("/costs/page.ai.yaml"));
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/build 1/);

    // The page you left finishes. The one you are watching is still building.
    gates[0]();
    await new Promise((r) => setTimeout(r, 60));

    expect(screen.getByRole("button", { name: /building/i })).toBeDisabled();
    gates[1]();
  });

  it("aborts the build when the pane moves to another page", async () => {
    // `stale()` stops the pane ACTING on the build; on its own it leaves the
    // build running on the server, because a client that stops reading a body
    // has told the server nothing. Both matter: the second one is what stops a
    // `pnpm install` from finishing into a folder nobody is watching, and what
    // stops going back starting a second build beside the first.
    const files = {
      "/sales/index.html": "<html><body>sales</body></html>",
      "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
      "/costs/index.html": "<html><body>costs</body></html>",
    };
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: RequestInit) => {
        if (!String(url).includes("/wui/build")) return new Response("{}", { status: 404 });
        signal = init?.signal ?? undefined;
        const encode = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              c.enqueue(encode.encode(sse({ type: "output", text: "resolving" })));
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }),
    );

    const at = (path: string) => (
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc(files)}>
            <WuiView path={path} spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>
    );

    const view = render(at("/sales/page.ai.yaml"));
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    await screen.findByText(/resolving/);
    expect(signal?.aborted).toBe(false);

    view.rerender(at("/costs/page.ai.yaml"));

    await waitFor(() => expect(signal?.aborted).toBe(true));
  });

  it("caps the log against the pane, not against a box the log itself sizes", async () => {
    // Measured in a browser: the strip was 424px tall in a 692px pane while
    // holding a 28px button and a 127px log — 270px of blank, and the page
    // squeezed into a third of the pane.
    //
    // `max-height: 30%` needs a containing block with a DEFINITE height. Wrapping
    // the log to give it a summary line put the cap on a box whose own height
    // comes from its content, so the browser sized the wrapper to the log's
    // UNCLAMPED content, then clamped the log against that — and never went back.
    //
    // The cap belongs on the flex ITEM (the pane's height is definite); the log
    // takes what is left and scrolls. `minHeight: 0` because a flex item's
    // default `min-height: auto` refuses to shrink below its content, which
    // would put the overflow back on the pane.
    const { release } = serveGated([
      sse({ type: "output", text: "vite build" }),
      sse({ type: "done", exit_code: 0 }),
    ]);
    renderIn({ ...BUILT });
    fireEvent.click(await screen.findByRole("button", { name: /rebuild/i }));
    release();
    fireEvent.click(await screen.findByRole("button", { name: /show build output/i }));

    const log = await screen.findByRole("log", { name: "Build output" });
    const strip = log.parentElement as HTMLElement;

    expect(strip.style.maxHeight).toBe("30%");
    expect(strip.style.display).toBe("flex");
    expect(strip.style.flexDirection).toBe("column");
    // The log is sized by the strip, not by a percentage of it.
    expect(log.style.maxHeight).toBe("");
    expect(log.style.minHeight).toBe("0");
    expect(log.style.overflowY).toBe("auto");
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

  function renderIn(files: Record<string, string>, chrome?: WuiChrome) {
    const fs = svc(files);
    const view = render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={fs}>
            <WuiView
              path="/sales/page.ai.yaml"
              spec={{ view: "wui", entity: "" } as ViewSpec}
              chrome={chrome}
            />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
    return { ...view, fs };
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

  it("never rebuilds on a reader's account", async () => {
    /**
     * The test above is this one's positive control: same folder, same
     * setting (on by default), and in workspace chrome the build fires. A
     * reader — somebody who followed the page's own URL — is handed what is
     * already built, never a build: tens of seconds and a sandbox woken on
     * behalf of someone who only came to look (docs/plan-wui-deploy.md).
     *
     * A reader costs none of it: not the build, and not the manifest read
     * whose only consumers are the author's controls (review round 1). So the
     * negative is anchored on the page being OPEN — the only read a reader
     * makes has landed — and then asserts that neither the probe nor the
     * build was ever asked for. That is stronger than "no build yet", which
     * could pass for a page that simply had not finished opening.
     */
    const files = { ...BUILT };
    serveBuild(sse({ type: "done", exit_code: 0 }));
    const { fs } = renderIn(files, "viewer");

    await waitFor(() => expect(frame()).toBeInTheDocument());
    await act(async () => {});

    expect(fs.readFile).not.toHaveBeenCalledWith("/sales/package.json");
    expect(buildCalls()).toHaveLength(0);
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
    const calls = buildCalls();
    // eslint-disable-next-line no-console
    console.log("DBG", calls.map(([, i]) => ({
      hasInit: !!i, hasSignal: !!(i as RequestInit)?.signal,
      aborted: (i as RequestInit)?.signal?.aborted,
    })));
    const live = buildCalls().filter(([, init]) => !(init as RequestInit)?.signal?.aborted);
    expect(live).toHaveLength(1);
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
    const toggle = await screen.findByLabelText(/rebuild this page whenever you open it/i);

    fireEvent.click(toggle); // on
    await new Promise((r) => setTimeout(r, 50));

    expect(getWuiAutoBuild(autoBuildScope("item1", "/sales"))).toBe(true);
    expect(buildCalls()).toHaveLength(0);
  });

  it("leaves exactly one build alive when React runs the effect twice", async () => {
    // StrictMode mounts, unmounts and mounts again, and the app runs in it. What
    // must never happen is two builds ALIVE in one folder, racing over `dist/`
    // and interleaving in the log — not two requests: the cleanup aborts the
    // first pass's build, and the second pass starts one because a cleanup that
    // could not be redone would leave the page never built at all.
    //
    // The manifest read is primed on purpose: with a COLD cache the two passes
    // are separated by an await and none of this is exercised. Warm — every
    // visit after the first — they run back to back.
    const client = makeTestQueryClient();
    // Primed in the shape the manifest query holds — the three-outcome
    // reader's `AssetRead`, not the bare text it used to be.
    client.setQueryData(qk.wuiBuildable("item1", "/sales"), {
      kind: "asset",
      asset: { kind: "text", text: JSON.stringify({ scripts: { build: "vite build" } }) },
    });
    serveBuild(sse({ type: "output", text: "built" }) + sse({ type: "done", exit_code: 0 }));
    render(
      <StrictMode>
        <QueryWrap client={client}>
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
    // Not "one request": the cleanup between the two passes aborts the first
    // build, and the second pass starts one because a cleanup that could not be
    // redone would leave the page never built at all. What must never happen is
    // two builds ALIVE in one folder.
    const live = buildCalls().filter(([, init]) => !(init as RequestInit)?.signal?.aborted);
    expect(live).toHaveLength(1);
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

    const toggle = await screen.findByLabelText(/rebuild this page whenever you open it/i);
    // A SWITCH, not a checkbox: it takes effect the moment it is flipped, and a
    // checkbox reads as a choice that has not happened yet.
    expect(toggle).toHaveAttribute("role", "switch");
    // The tooltip and the accessible name are the SAME sentence: a mouse and a
    // screen reader should not be told two different things about one control.
    expect(toggle.closest("label")).toHaveAttribute(
      "title",
      toggle.getAttribute("aria-label"),
    );
    // Readable on its own: "on open" said nothing to anyone who had not just
    // read the code. The Rebuild button beside it establishes the word, so the
    // box is that word made automatic.
    expect(toggle.closest("label")).toHaveTextContent(/^Auto-rebuild$/);
  });

  it("lets the viewer turn it off from the pane", async () => {
    // The user's own choice, in front of them — not a hidden default.
    serveBuild(sse({ type: "done", exit_code: 0 }));
    renderIn({ ...BUILT });

    const toggle = await screen.findByLabelText(/rebuild this page whenever you open it/i);
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    expect(getWuiAutoBuild(autoBuildScope("item1", "/sales"))).toBe(false);
  });
});

describe("WuiView: Deploy", () => {
  /**
   * Deploy = rebuild, then hand over the page's own address. The publisher
   * builds; the reader (WuiPage, `chrome="viewer"`) never does — so the
   * address appears only once what it points at is fresh
   * (docs/plan-wui-deploy.md, P2).
   */
  const PLAIN = { "/sales/index.html": "<html><body>v1</body></html>" };
  const BUILT = {
    ...PLAIN,
    "/sales/package.json": JSON.stringify({ scripts: { build: "vite build" } }),
  };
  const ADDRESS = `${window.location.origin}/w/rca/item1/sales/page.ai.yaml`;

  /** The workspace-chrome pane on `/sales/page.ai.yaml`; hands the service
   * back and lets a test wrap its reads. */
  function renderInFs(
    files: Record<string, string>,
    wrapRead?: (path: string, real: FileService["readFile"]) => ReturnType<FileService["readFile"]>,
  ) {
    const fs = svc(files);
    if (wrapRead) {
      const real = fs.readFile;
      (fs as { readFile: FileService["readFile"] }).readFile = vi.fn((path: string) => wrapRead(path, real));
    }
    const view = render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={fs}>
            <WuiView path="/sales/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
    return { ...view, fs };
  }
  const renderIn = (files: Record<string, string>) => renderInFs(files);

  const sse = (payload: unknown) => `data: ${JSON.stringify(payload)}\n\n`;
  const buildCalls = () =>
    vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/wui/build"));

  // The manual path: rebuilding on open is on by default, and a test about the
  // button has to say it is not testing the automatic one.
  beforeEach(() => setWuiAutoBuild(autoBuildScope("item1", "/sales"), false));
  const realClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    if (realClipboard) Object.defineProperty(navigator, "clipboard", realClipboard);
    else delete (navigator as { clipboard?: unknown }).clipboard;
  });

  it("keeps Rebuild and Auto-rebuild out of reach while it runs, so nothing builds beside it", async () => {
    /**
     * Review round 2: Rebuild was disabled only on `building`, and Deploy has
     * phases where nothing is building yet — the manifest re-read, the open
     * check. Rebuild pressed in one of them started a second `pnpm run build`
     * in the same folder, both writing `dist/`, and whichever finished first
     * re-read the folder over the other's half-written output.
     */
    let answer: () => void = () => {};
    const gate = new Promise<void>((r) => (answer = r));
    const { release } = serveHeldBuild(0);
    let manifestReads = 0;
    renderInFs({ ...BUILT }, async (path, real) => {
      // Hold the manifest re-read that Deploy makes on press — the first read,
      // on open, goes through so Rebuild is on screen to be pressed.
      if (path.endsWith("package.json") && ++manifestReads > 1) await gate;
      return real(path);
    });
    await screen.findByRole("button", { name: /^rebuild$/i });

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    // Mid re-read: nothing is building, and still nothing else may start.
    expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeDisabled();
    expect(screen.getByRole("switch", { name: /rebuild this page whenever/i })).toBeDisabled();
    answer();
    await screen.findByText(/> vite build/);
    release();
    await screen.findByRole("textbox", { name: /address/i });
    expect(buildCalls()).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled();
  });

  it("is this open's build — Auto-rebuild does not start another one beside it", async () => {
    /**
     * Review round 2: Deploy's manifest re-read can flip `canBuild` from
     * false to true, and the rebuild-on-open effect (whose guard was never
     * set while `canBuild` was false) then fired its own automatic build
     * next to Deploy's. Two builds, one folder. Auto-rebuild is ON here — the
     * default — because that is the setting under which it happened.
     */
    setWuiAutoBuild(autoBuildScope("item1", "/sales"), true);
    const files: Record<string, string> = { ...PLAIN };
    const { release } = serveHeldBuild(0);
    const { fs } = renderInFs(files);
    await waitFor(() => expect(frame()).toBeInTheDocument());
    await waitFor(() => expect(fs.readFile).toHaveBeenCalledWith("/sales/package.json"));

    files["/sales/package.json"] = BUILT["/sales/package.json"];
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    release();
    await screen.findByRole("textbox", { name: /address/i });
    await act(async () => {});

    expect(buildCalls()).toHaveLength(1);
  });

  it("forgets a verdict when the pane moves to a sibling page in the same folder", async () => {
    /**
     * Review round 2: the address is computed from `path` on every render
     * while the verdict was reset only on `folder` — so after deploying
     * `a.ai.yaml`, switching the same pane to `b.ai.yaml` (same folder, an
     * entry nobody built) showed "✓ Deployed" over B's address: a link that
     * lands on "not published yet".
     */
    vi.stubGlobal("fetch", vi.fn());
    const files = { ...PLAIN };
    const page = pages(svc(files), makeTestQueryClient()); // one client across the rerender
    const view = render(page("/sales/a.ai.yaml"));
    fireEvent.click(await screen.findByRole("button", { name: /^deploy$/i }));
    await screen.findByRole("textbox", { name: /address/i });

    view.rerender(page("/sales/b.ai.yaml", "dist/index.html"));

    await waitFor(() => expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull());
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
  });

  it("shows the page it verified — the pane and the verdict cannot disagree", async () => {
    /**
     * Review round 2: the open check was a second, independent read outside
     * the pane's own query. A page opened before its `index.html` existed
     * showed the red "no index.html" error; the file was written; Deploy's
     * private read passed and "✓ Deployed" sat directly above the red error,
     * because nothing had told the pane to look again. One read now, through
     * the pane's query: what Deploy verified is what the pane shows.
     */
    vi.stubGlobal("fetch", vi.fn());
    const files: Record<string, string> = {};
    const { fs } = renderInFs(files);
    expect(await screen.findByRole("status")).toHaveTextContent("index.html");
    expect(frame()).toBeNull();

    files["/sales/index.html"] = PLAIN["/sales/index.html"];
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    await screen.findByRole("textbox", { name: /address/i });
    await waitFor(() => expect(frame()).toBeInTheDocument());
    // The red error is gone — the pane re-read the folder along with the
    // verdict. (The Deploy panel is itself a `status`, so it is the sentence
    // that is asserted absent, not the role.)
    expect(screen.queryByText(/no index\.html to open/)).toBeNull();
    // ONE read of the entry for the verdict and the pane together, not two.
    expect(vi.mocked(fs.readFile).mock.calls.filter(([p]) => p === "/sales/index.html")).toHaveLength(2);
  });

  /** One QueryClient across rerenders, as production has one — `QueryWrap`
   * without a client makes a NEW one per render, which silently defeats any
   * test about the cache outliving an instance. */
  function pages(fs: FileService, client: QueryClient) {
    return (p: string, entry?: string) => (
      <QueryWrap client={client}>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={fs}>
            <WuiView path={p} spec={{ view: "wui", entity: "", ...(entry ? { entry } : {}) } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>
    );
  }

  it("reads the folder again after the pane was remounted — never a cached verdict", async () => {
    /**
     * Review round 6: keying the pane by folder (round 5) made a cross-folder
     * move a remount, and a remount starts `generation` at 0 — while the old
     * instance's `(scopeId, path, generation)` documents are still in the
     * cache for five minutes, never stale. Deploy's `fetchQuery` for
     * generation 1 hit the PREVIOUS Deploy's document and said "✓ Deployed"
     * without reading the folder at all. Every instance keys its documents by
     * its own nonce, so no instance can ever hit another's.
     */
    vi.stubGlobal("fetch", vi.fn());
    setWuiAutoBuild(autoBuildScope("item1", "/b"), false);
    const client = makeTestQueryClient();
    const files: Record<string, string> = {
      "/a/index.html": "<html><body>a</body></html>",
      "/b/index.html": "<html><body>b</body></html>",
    };
    const page = pages(svc(files), client);
    const view = render(page("/a/page.ai.yaml"));
    fireEvent.click(await screen.findByRole("button", { name: /^deploy$/i }));
    await screen.findByRole("textbox", { name: /address/i });

    view.rerender(page("/b/page.ai.yaml"));
    await waitFor(() => expect(frame()).toBeInTheDocument());
    view.rerender(page("/a/page.ai.yaml"));
    await waitFor(() => expect(frame()).toBeInTheDocument());

    // The agent broke the page while the pane was away.
    delete files["/a/index.html"];
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    expect(await screen.findByText(/deploy failed/i)).toHaveTextContent(/does not open/i);
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("is a new pane for the same folder in another item", async () => {
    /**
     * Review round 6: the key was the folder alone. Two items of one App
     * share template paths (`dashboard/page.ai.yaml`), so switching item
     * reused the instance — build log, verdict, the running build and
     * `autoBuiltFor` all carried over, and the new item's rebuild-on-open was
     * silently skipped.
     */
    vi.stubGlobal("fetch", vi.fn());
    const client = makeTestQueryClient();
    const files = { ...PLAIN };
    const pageIn = (scopeId: string) => {
      const fs = svc({ ...files });
      (fs as { scopeId: string }).scopeId = scopeId;
      return pages(fs, client)("/sales/page.ai.yaml");
    };
    const view = render(pageIn("item1"));
    fireEvent.click(await screen.findByRole("button", { name: /^deploy$/i }));
    await screen.findByRole("textbox", { name: /address/i });

    view.rerender(pageIn("item2"));
    await waitFor(() => expect(frame()).toBeInTheDocument());

    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("does not reload a sibling page under someone's hands — the reload waits for the page it is about", async () => {
    /**
     * Review round 6, citing plan decision 9 (nothing reloads a page on its
     * own — a person halfway through a form is not yanked out of it). Round 3
     * had A's finished build reload sibling B; that was the decision
     * violated. A's verdict and A's reload now wait until the pane is on A
     * again — arriving there is a fresh open, not an interruption.
     */
    const { release } = serveHeldBuild(0);
    const client = makeTestQueryClient();
    const files: Record<string, string> = {
      ...BUILT,
      "/sales/dist/index.html": "<html><body>v1</body></html>",
    };
    const fs = svc(files);
    const page = pages(fs, client);
    const distReads = () => vi.mocked(fs.readFile).mock.calls.filter(([p]) => p === "/sales/dist/index.html").length;
    const view = render(page("/sales/a.ai.yaml", "dist/index.html"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);

    view.rerender(page("/sales/b.ai.yaml", "dist/index.html"));
    await waitFor(() => expect(frame()).toBeInTheDocument());
    const bFrame = frame();
    const readsBeforeRelease = distReads();

    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled());
    await act(async () => {});

    // B: same frame, same document, nothing re-read on its account.
    expect(frame()).toBe(bFrame);
    expect(distReads()).toBe(readsBeforeRelease + 1); // Deploy's own verify read of the folder, nothing for B
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.getByRole("button", { name: /^deploy$/i })).toHaveTextContent(/^deploy$/i);

    // Back on A: the verdict, and the document it verified.
    view.rerender(page("/sales/a.ai.yaml", "dist/index.html"));
    expect(await screen.findByText(/^✓ deployed/i)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /address/i })).toHaveValue(
      `${window.location.origin}/w/rca/item1/sales/a.ai.yaml`,
    );
  });

  it("follows the manifest: a build script removed mid-session makes this a plain page", async () => {
    /**
     * Review round 7: round 6 had guessed that a manifest "there a moment ago
     * and not now" was a folder mid-restore, and put the old answer back —
     * which made a DELIBERATELY removed `package.json` a permanent failure:
     * every Deploy refused, Rebuild drawn over a page with no build, until a
     * tab reload. The pane's knowledge of the manifest now follows the file
     * (the `fileChangedBus` effect), and a fresh "not there" is what it says.
     */
    vi.stubGlobal("fetch", vi.fn());
    const files: Record<string, string> = { ...BUILT };
    renderInFs(files);
    await screen.findByRole("button", { name: /^rebuild$/i });

    // The agent converts the page to a plain one.
    delete files["/sales/package.json"];
    act(() => publishFileChanged("item1", "/sales/package.json"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^rebuild$/i })).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(ADDRESS);
    expect(buildCalls()).toHaveLength(0);
  });

  it("reads the folder afresh on the next Deploy after a cancelled verify", async () => {
    /**
     * Review round 9: a Deploy cancelled during its open check still let that
     * read finish and cache its document under `shown + 1` — the number the
     * NEXT run would verify under, which then hit the cache and said
     * "✓ Deployed" without reading the folder it had just rebuilt. Each run
     * verifies under a number of its own, and reads fresh.
     */
    vi.stubGlobal("fetch", vi.fn());
    let hold: () => void = () => {};
    const gate = new Promise<void>((r) => (hold = r));
    let indexReads = 0;
    const files: Record<string, string> = { ...PLAIN };
    renderInFs(files, async (path, real) => {
      // The verify read of the first Deploy is held open.
      if (path === "/sales/index.html" && ++indexReads === 2) await gate;
      return real(path);
    });
    await waitFor(() => expect(frame()).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByRole("button", { name: /^cancel$/i });
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^deploy$/i })).toBeEnabled());

    // The page breaks; the held read then completes with the OLD document.
    delete files["/sales/index.html"];
    hold();
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    expect(await screen.findByText(/deploy failed/i)).toHaveTextContent(/does not open/i);
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("does not start a build because the manifest changed under an open page", async () => {
    /**
     * Review round 9: the manifest is re-read when the file changes now, so
     * the agent scaffolding a `package.json` mid-session flipped `canBuild`
     * — and the rebuild-on-open effect, whose guard had never been set for a
     * plain page, started a build under the page someone was using. The
     * opening moment is spent once the manifest has ANSWERED, whatever it
     * said; nobody opened the page again.
     */
    setWuiAutoBuild(autoBuildScope("item1", "/sales"), true);
    vi.stubGlobal("fetch", vi.fn());
    const files: Record<string, string> = { ...PLAIN };
    renderInFs(files);
    await waitFor(() => expect(frame()).toBeInTheDocument());
    await act(async () => {});

    files["/sales/package.json"] = BUILT["/sales/package.json"];
    act(() => publishFileChanged("item1", "/sales/package.json"));
    await screen.findByRole("button", { name: /^rebuild$/i }); // the toolbar learned it
    await act(async () => {});

    expect(buildCalls()).toHaveLength(0);
    expect(frame()).toBeInTheDocument(); // the page was not replaced by "Building…"
  });

  it("gives the on-open claim back on Cancel, so toggling Auto-rebuild is not an open", async () => {
    // Review round 9: Cancel cleared `autoBuiltFor` outright, and the next
    // flip of the switch counted as opening the page and started a build.
    serveHeldBuild(0);
    renderIn({ ...BUILT });
    await screen.findByRole("button", { name: /^rebuild$/i });
    await act(async () => {}); // the opening moment is spent (Auto-rebuild is off here)
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^deploy$/i })).toBeEnabled());
    const before = buildCalls().length;

    fireEvent.click(screen.getByRole("switch", { name: /rebuild this page whenever/i })); // on
    await act(async () => {});

    expect(buildCalls()).toHaveLength(before);
  });

  it("takes Rebuild away when the manifest is renamed out of the way", async () => {
    // Review round 9: a move publishes its DESTINATION, so an exact match on
    // the manifest's path missed a `package.json` renamed to `.bak`.
    vi.stubGlobal("fetch", vi.fn());
    const files: Record<string, string> = { ...BUILT };
    renderInFs(files);
    await screen.findByRole("button", { name: /^rebuild$/i });

    delete files["/sales/package.json"];
    files["/sales/package.json.bak"] = BUILT["/sales/package.json"];
    act(() => publishFileChanged("item1", "/sales/package.json.bak"));

    await waitFor(() => expect(screen.queryByRole("button", { name: /^rebuild$/i })).toBeNull());
  });

  it("can be cancelled — a build that never ends does not hold the pane forever", async () => {
    /**
     * Review round 8: the hold had no way out and no bound. A `pnpm run
     * build` that hangs, or a gateway holding the SSE open, kept Refresh,
     * Rebuild and Deploy disabled for the life of the pane; closing the file
     * was the only exit — the one failure nothing can report.
     */
    serveHeldBuild(0); // never released: the build hangs
    renderIn({ ...BUILT });
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /^refresh$/i })).toBeEnabled());
    expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^deploy$/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^cancel$/i })).toBeNull();
    // The build request was aborted, not left running on the server's side.
    const live = buildCalls().filter(([, init]) => !(init as RequestInit)?.signal?.aborted);
    expect(live).toHaveLength(0);
    expect(screen.queryByText(/deployed|deploy failed/i)).toBeNull();
  });

  it("does not promise a sibling page a reload that A's Deploy will not do", async () => {
    /**
     * Review round 8: a sibling B with a red entry error read "Building… the
     * page appears when this finishes" while A's Deploy built — but a Deploy
     * reloads only the page it is about, so when the build ended B's error
     * simply came back. The placeholder is for a page that WILL be reloaded.
     */
    const { release } = serveHeldBuild(0);
    const client = makeTestQueryClient();
    const files: Record<string, string> = { ...BUILT }; // no dist/ yet
    const page = pages(svc(files), client);
    const view = render(page("/sales/a.ai.yaml", "dist/index.html"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);

    view.rerender(page("/sales/b.ai.yaml", "dist/index.html"));

    // B's own read lands (it is a new key), and what it found is what shows —
    // not a "Building…" placeholder for a build that will not reload B.
    expect(await screen.findByText(/no dist\/index\.html to open/)).toBeInTheDocument();
    expect(screen.queryByText(/the page appears when this finishes/i)).toBeNull();
    release();
  });

  it("retires a waiting verdict when a Rebuild reloads the folder in the meantime", async () => {
    /**
     * Review round 8: A's verdict waited (gen N) while the pane was on B; a
     * Rebuild pressed on B then landed on the same generation N. Back on A,
     * the verdict applied and the frame showed Deploy's cached, PRE-rebuild
     * document under "✓ Deployed" — while the address served the rebuilt
     * one. A build the whole folder reflects retires what waited.
     */
    const { release } = serveHeldBuild(0);
    const client = makeTestQueryClient();
    const files: Record<string, string> = {
      ...BUILT,
      "/sales/dist/index.html": "<html><body>v1</body></html>",
    };
    const page = pages(svc(files), client);
    const view = render(page("/sales/a.ai.yaml", "dist/index.html"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    view.rerender(page("/sales/b.ai.yaml", "dist/index.html"));
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled());

    // On B, the author edits the source and rebuilds the folder: dist/ is v2.
    files["/sales/dist/index.html"] = "<html><body>v2</body></html>";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(sse({ type: "done", exit_code: 0 }), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      })),
    );
    fireEvent.click(screen.getByRole("button", { name: /^rebuild$/i }));
    await screen.findByText(/Build finished/);

    view.rerender(page("/sales/a.ai.yaml", "dist/index.html"));
    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    // And the FRAME is the rebuilt document, not Deploy's cached pre-rebuild
    // one: the Rebuild's generation must never land on the number Deploy
    // verified under (review round 9 — each run verifies under its own).
    await waitFor(() => expect(frame()?.getAttribute("srcdoc")).toContain("v2"));
  });

  it("is not disturbed by an `entry:` edit under a running Deploy — the frame is not reloaded on its own", async () => {
    /**
     * Review round 9, on plan decision 9: round 7 had put `entry` into the
     * document's key, so an edited `entry:` re-read and reloaded the frame
     * by itself — the thing the decision forbids — and round 8 then had a
     * running Deploy's verdict fail because of it. Neither now: the document
     * is keyed by path and generation, an entry edit changes nothing until
     * Refresh, and the verdict lands on the document the run verified.
     */
    const { release } = serveHeldBuild(0);
    const client = makeTestQueryClient();
    const files: Record<string, string> = {
      ...BUILT,
      "/sales/dist/index.html": "<html><body>v1</body></html>",
    };
    const fs = svc(files);
    const page = pages(fs, client);
    const view = render(page("/sales/page.ai.yaml", "index.html"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);

    // The author edits the view file's entry while the build runs.
    view.rerender(page("/sales/page.ai.yaml", "dist/index.html"));
    const distReadsBefore = vi.mocked(fs.readFile).mock.calls.filter(([p]) => p === "/sales/dist/index.html").length;
    release();

    expect(await screen.findByText(/^✓ deployed/i)).toBeInTheDocument();
    await act(async () => {});
    // No read of the new entry happened on the edit's account.
    expect(vi.mocked(fs.readFile).mock.calls.filter(([p]) => p === "/sales/dist/index.html")).toHaveLength(
      distReadsBefore,
    );
  });

  it("drops a waiting verdict whose document has left the cache, rather than claim a fresh read", async () => {
    /**
     * Review round 7: a verdict that waited on a sibling pointed at a
     * document nothing observed — gone after `gcTime` — and returning to the
     * page then pointed the pane at a key that had to be read again, under a
     * "✓ Deployed" that claimed the frame showed what was verified.
     */
    const { release } = serveHeldBuild(0);
    const client = makeTestQueryClient();
    const files: Record<string, string> = { ...BUILT, "/sales/dist/index.html": "<html><body>v1</body></html>" };
    const page = pages(svc(files), client);
    const view = render(page("/sales/a.ai.yaml", "dist/index.html"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    view.rerender(page("/sales/b.ai.yaml", "dist/index.html"));
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled());

    // Five minutes pass on B: the verified document is collected.
    client.removeQueries({ queryKey: ["wuiDoc"] });

    view.rerender(page("/sales/a.ai.yaml", "dist/index.html"));
    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
    // And not in silence (review round 8): the author is told why there is
    // no address, rather than seeing a Deploy that ended with nothing.
    expect(screen.getByText(/deploy stopped/i)).toHaveTextContent(/expire|changed/i);
  });

  it("starts clean on a page in another folder while a Deploy was running", async () => {
    /**
     * Review round 5 (reproduced): with a Deploy in flight, moving the same
     * pane to a page in ANOTHER folder aborted the build, every `moved()`
     * returned before a `setDeploy`, and the state stayed `working` — the
     * new page's Refresh, Rebuild and Deploy were held forever, the button
     * reading "Deploying…" until the whole view unmounted. Cross-folder is a
     * remount now (the pane is keyed by folder), so no hand-written reset
     * can forget a state again.
     */
    // The build is HELD, as a real 30-second build is: the assertions below
    // are made while it is still running. (A first version of this test let
    // the old run finish at once, and passed with the key removed — for the
    // wrong reason. Found by mutation, not by review.)
    const { release } = serveHeldBuild(0);
    // The manual path for the destination folder too — `beforeEach` only
    // covers `/sales`, and a fresh `/reports` pane would otherwise start its
    // own rebuild-on-open, which is a hold of its own, not the leak under test.
    setWuiAutoBuild(autoBuildScope("item1", "/reports"), false);
    const files = {
      ...BUILT,
      "/reports/index.html": "<html><body>r</body></html>",
      "/reports/package.json": BUILT["/sales/package.json"],
    };
    const fs = svc(files);
    // ONE client across the rerender (`QueryWrap` without one makes a fresh
    // client per render, which quietly defeats anything about the cache).
    const page = pages(fs, makeTestQueryClient());
    const view = render(page("/sales/page.ai.yaml"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    expect(screen.getByRole("button", { name: /deploying/i })).toBeInTheDocument();

    view.rerender(page("/reports/dash.ai.yaml"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    await act(async () => {});

    // Still mid-build for /sales — and the /reports page owes it nothing.
    expect(screen.getByRole("button", { name: /^deploy$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /deploying/i })).toBeNull();
    expect(screen.queryByText(/> vite build/)).toBeNull(); // nor its log
    release();
  });

  it("fails, and says so, when it cannot check for a build — never 'nothing to build'", async () => {
    /**
     * Review round 5 (reproduced): the manifest query's `.catch(() => "")`
     * turned a dropped connection, a 5xx, or a mid-restore 404 into "no
     * build" — Deploy skipped the build, verified the OLD `dist/` and said
     * "✓ Deployed", while the cache's correct answer was overwritten with ""
     * and Rebuild vanished from the toolbar. Deploy reads the manifest
     * through the three-outcome reader: absent is "no build", a failed read
     * is a failed Deploy.
     */
    vi.stubGlobal("fetch", vi.fn());
    let manifestReads = 0;
    renderInFs({ ...BUILT }, async (path, real) => {
      if (path === "/sales/package.json" && ++manifestReads > 1) throw new TypeError("Failed to fetch");
      return real(path);
    });
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    const said = await screen.findByText(/deploy failed/i);
    expect(said).toHaveTextContent(/could not check/i);
    expect(buildCalls()).toHaveLength(0);
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
    // The toolbar still knows the page has a build.
    expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeInTheDocument();
  });

  it("forgets a FAILED verdict on Refresh too — it was about one read as well", async () => {
    /**
     * Review round 5 (reproduced): round 4 tied the SUCCESS verdict to the
     * generation it verified and left the failed one to live forever — a red
     * "the page does not open" stayed above a page that, after the agent
     * wrote `index.html` and Refresh read it, opened fine.
     */
    vi.stubGlobal("fetch", vi.fn());
    const files: Record<string, string> = { "/sales/README.md": "nothing" };
    renderInFs(files);
    fireEvent.click(await screen.findByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/deploy failed/i);

    files["/sales/index.html"] = PLAIN["/sales/index.html"];
    fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));

    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(screen.queryByText(/deploy failed/i)).toBeNull();
  });

  it("holds the sibling page too — the lock is the pane's, the verdict is the page's", async () => {
    /**
     * Review round 4: keying the state by path (round 3) also keyed the HOLD
     * by path, so while A deployed, the sibling B in the same folder had
     * Rebuild, Refresh and Deploy live again — two builds in one folder came
     * back through the sibling. What is shown is the page's; what is held is
     * the whole pane's.
     */
    let answer: () => void = () => {};
    const gate = new Promise<void>((r) => (answer = r));
    const { release } = serveHeldBuild(0);
    const files = { ...BUILT };
    const fs = svc(files);
    const real = fs.readFile;
    let manifestReads = 0;
    (fs as { readFile: FileService["readFile"] }).readFile = vi.fn(async (path: string) => {
      if (path.endsWith("package.json") && ++manifestReads > 1) await gate;
      return real(path);
    });
    // ONE client across the rerender (`QueryWrap` without one makes a fresh
    // client per render, which quietly defeats anything about the cache).
    const page = pages(fs, makeTestQueryClient());
    const view = render(page("/sales/a.ai.yaml"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    view.rerender(page("/sales/b.ai.yaml"));
    expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /deploy/i })).toBeDisabled();

    answer();
    await screen.findByText(/> vite build/);
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /^rebuild$/i })).toBeEnabled());
    expect(buildCalls()).toHaveLength(1);
  });

  it("forgets the verdict on Refresh — it was about the generation it verified", async () => {
    /**
     * Review round 4: "✓ Deployed" never expired. Deploy verified generation
     * g; the agent deleted `index.html`; Refresh read generation g+1 and the
     * pane went red — under a panel still offering the address. A verdict is
     * a fact about one read, so it is shown only while that read is what the
     * pane shows.
     */
    vi.stubGlobal("fetch", vi.fn());
    const files: Record<string, string> = { ...PLAIN };
    renderInFs(files);
    fireEvent.click(await screen.findByRole("button", { name: /^deploy$/i }));
    await screen.findByRole("textbox", { name: /address/i });

    delete files["/sales/index.html"];
    fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));

    await screen.findByText(/no index\.html to open/);
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("does not start a build for a pane that has been closed", async () => {
    /**
     * Review round 4: the unmount cleanup aborted a build in flight but did
     * not bump the epoch, so a Deploy still in its manifest re-read woke up
     * with `moved()` false and STARTED a build — for a page nobody was
     * looking at, with no one left to abort it.
     */
    let answer: () => void = () => {};
    const gate = new Promise<void>((r) => (answer = r));
    serveHeldBuild(0);
    let manifestReads = 0;
    const view = renderInFs({ ...BUILT }, async (path, real) => {
      if (path.endsWith("package.json") && ++manifestReads > 1) await gate;
      return real(path);
    });
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    view.unmount();
    answer();
    await act(async () => {});
    await act(async () => {});

    expect(buildCalls()).toHaveLength(0);
  });

  it("leaves the pane alone when the page does not open — no second read the verdict never saw", async () => {
    /**
     * Review round 3: on the failure path the pane was pointed at the failed
     * generation, and a query in error with no data refetches on the key
     * switch — a SECOND read the verdict never saw. During a sandbox restore
     * that read can succeed, and the page then rendered directly under a red
     * "the page does not open". The verdict is about the read Deploy made;
     * the pane is not touched by a failure.
     */
    vi.stubGlobal("fetch", vi.fn());
    const { fs } = renderInFs({ "/sales/README.md": "nothing to open here" });
    await screen.findByRole("status");
    const reads = () => vi.mocked(fs.readFile).mock.calls.filter(([p]) => p === "/sales/index.html").length;
    const beforeDeploy = reads();

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/deploy failed/i);
    await act(async () => {});

    // Exactly one read for the verdict, and none for the pane.
    expect(reads()).toBe(beforeDeploy + 1);
  });

  it("names the page a hold is for on the sibling's button, so its Cancel is not a Cancel for nothing", async () => {
    /**
     * Review round 6: the button's LABEL came from the pane-wide hold, so
     * while A deployed, the sibling B's button read "Deploying…" — a claim
     * about A hung under B's name. Round 9: a bare "Deploy" (disabled) beside
     * a live "Cancel" was the opposite failure — a Cancel with nothing on
     * screen saying what it cancels. The hold stays pane-wide; the word names
     * the page it is about.
     *
     * (This replaces a round-3 test that had A's finished build reload B —
     * the behaviour plan decision 9 forbids; see "does not reload a sibling
     * page under someone's hands".)
     */
    const { release } = serveHeldBuild(0);
    const client = makeTestQueryClient();
    const page = pages(svc({ ...BUILT }), client);
    const view = render(page("/sales/a.ai.yaml"));
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);
    expect(screen.getByRole("button", { name: /^deploying…$/i })).toBeInTheDocument();

    view.rerender(page("/sales/b.ai.yaml"));

    const b = screen.getByRole("button", { name: /^deploying a\.ai\.yaml…$/i });
    expect(b).toBeDisabled();
    expect(screen.getByRole("button", { name: /^cancel$/i })).toHaveAttribute("title", "Stop deploying a.ai.yaml");
    release();
  });

  it("holds Refresh too while it runs — the pane is Deploy's until the verdict is in", async () => {
    /**
     * Review round 3: Refresh was not held, and Deploy's final generation
     * write was absolute — two Refreshes during the open check moved the
     * generation forward, Deploy's write moved it back, and the next Refresh
     * landed on a key already cached as never-stale: a Refresh that did not
     * refresh. One rule: nothing else touches the pane while Deploy runs.
     */
    let answer: () => void = () => {};
    const gate = new Promise<void>((r) => (answer = r));
    vi.stubGlobal("fetch", vi.fn());
    let manifestReads = 0;
    renderInFs({ ...PLAIN }, async (path, real) => {
      if (path.endsWith("package.json") && ++manifestReads > 1) await gate;
      return real(path);
    });
    await waitFor(() => expect(frame()).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeDisabled();
    answer();
    await screen.findByRole("textbox", { name: /address/i });
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeEnabled();
  });

  it("is not offered where there is no slug to deploy under", async () => {
    /**
     * Review round 3: every workspace-chrome host drew Deploy, including ones
     * with no slug (a `view: wui` file opened in the KB IDE), where it sat
     * permanently disabled with no word why and its address would have read
     * `…/w//…`. Like `callTool`, it exists only where the slug does.
     */
    vi.stubGlobal("fetch", vi.fn());
    renderWui(PLAIN); // no WorkspaceSlugProvider
    await waitFor(() => expect(frame()).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: /^deploy$/i })).toBeNull();
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeInTheDocument();
  });

  it("hands over the page's address at once when there is nothing to build", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderIn(PLAIN);

    const deploy = await screen.findByRole("button", { name: /^deploy$/i });
    fireEvent.click(deploy);

    // The address, verbatim, in a field the publisher can select by hand —
    // it must match the route WuiPage answers (`/w/:slug/:itemId/*`).
    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(ADDRESS);
    expect(screen.getByText(/deployed/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open/i })).toHaveAttribute("href", ADDRESS);
    // Count the BUILD calls, not every fetch: the pane also asks who you are.
    expect(buildCalls()).toHaveLength(0);
  });

  /** A build whose end waits on a gate, so the moment BEFORE it finishes can
   * be looked at. */
  function serveHeldBuild(exitCode: number) {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const encode = new TextEncoder();
        return new Response(
          new ReadableStream<Uint8Array>({
            async start(controller) {
              controller.enqueue(encode.encode(sse({ type: "output", text: "> vite build" })));
              await gate;
              controller.enqueue(encode.encode(sse({ type: "done", exit_code: exitCode })));
              controller.close();
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }),
    );
    return { release };
  }

  it("builds first, and hands over the address only once the build has finished", async () => {
    const { release } = serveHeldBuild(0);
    renderIn(BUILT);

    // Rebuild appearing is the sign the manifest has been read and the page
    // is known to have a build — the moment Deploy is allowed to be pressed.
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    // Mid-build: the build is running, the button says so and refuses a
    // second press, and there is NO address yet — handing it over now would
    // point at the old `dist/`.
    await screen.findByText(/> vite build/);
    expect(buildCalls()).toHaveLength(1);
    const deploying = screen.getByRole("button", { name: /deploying/i });
    expect(deploying).toBeDisabled();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();

    release();

    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(ADDRESS);
    expect(screen.getByText(/deployed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^deploy$/i })).toBeEnabled();
  });

  it("names a build whose output stopped without a verdict, so 'see the build output' points at something", async () => {
    /**
     * Review round 2: a stream that closed without a `done` — a proxy's read
     * timeout on a long build, a connection cut mid-way — returned `false`
     * with no line written, and Deploy said "see the build output" over a
     * log whose last line was ordinary vite output.
     */
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(sse({ type: "output", text: "> vite build" }), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      })),
    );
    renderIn(BUILT);
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    expect(await screen.findByText(/deploy failed/i)).toHaveTextContent(/build output/i);
    expect(screen.getByText(/ended without a verdict/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("says the deploy failed, over the build's own words, and hands nothing over", async () => {
    /**
     * A failed build leaves the old `dist/` up (documented). Handing over the
     * address anyway would be a "Deployed" that points at the page from
     * before — worse than no address. The log stays open: the compiler's
     * error is the only explanation on screen.
     */
    const { release } = serveHeldBuild(1);
    renderIn(BUILT);
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    await screen.findByText(/> vite build/);

    release();

    expect(await screen.findByText(/deploy failed/i)).toBeInTheDocument();
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
    // The build's own verdict and output are still on screen.
    expect(screen.getByText(/Build failed \(exit 1\)/)).toBeInTheDocument();
    expect(screen.getByText(/> vite build/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^deploy$/i })).toBeEnabled();
  });

  it("copies the address, and says so when it could not", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const writeText = vi.fn(async () => {});
    // On the real Navigator, not a spread copy of it: spreading an instance
    // drops every prototype getter (`userAgent`, `language`, `onLine`), and a
    // code path reading one of those would behave differently in this test
    // alone. Restored in `afterEach` below.
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderIn(PLAIN);
    const deploy = await screen.findByRole("button", { name: /^deploy$/i });
    fireEvent.click(deploy);
    await screen.findByRole("textbox", { name: /address/i });

    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    await screen.findByRole("button", { name: /^copied$/i });
    expect(writeText).toHaveBeenCalledWith(ADDRESS);

    // Now with no clipboard to write to (a non-secure context): the button
    // must not pretend, and the field is already there to select from.
    writeText.mockRejectedValueOnce(new Error("NotAllowedError"));
    fireEvent.click(screen.getByRole("button", { name: /^copied$/i }));
    await screen.findByRole("button", { name: /copy failed/i });
    expect(screen.getByRole("textbox", { name: /address/i })).toHaveValue(ADDRESS);
  });

  it("encodes a folder name the address bar would otherwise break on", async () => {
    /**
     * A space or a CJK folder name is the ordinary case here, not the edge.
     * Encoded segment by segment — the `/` between them must survive as a
     * separator, and the router on the other end (`/w/:slug/:itemId/*`)
     * decodes each segment back; `WuiPage.test.tsx` holds that half.
     */
    vi.stubGlobal("fetch", vi.fn());
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc({ "/報告 v2/index.html": "<html><body>v1</body></html>" })}>
            <WuiView path="/報告 v2/page.ai.yaml" spec={{ view: "wui", entity: "" } as ViewSpec} />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
    const deploy = await screen.findByRole("button", { name: /^deploy$/i });
    fireEvent.click(deploy);

    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(
      `${window.location.origin}/w/rca/item1/%E5%A0%B1%E5%91%8A%20v2/page.ai.yaml`,
    );
  });

  it("does not say Deployed when the page does not open — no build to blame", async () => {
    /**
     * Review round 1: "Deployed" was declared on the build's exit code (or on
     * the no-build shortcut), never on whether the page OPENS. A plain page
     * with no `index.html` got "✓ Deployed" and an address rendered directly
     * above the red "no index.html to open" error — and the reader following
     * it saw "not published yet". Deployed has to mean the link works.
     */
    vi.stubGlobal("fetch", vi.fn());
    renderIn({ "/sales/README.md": "nothing to open here" });
    const deploy = await screen.findByRole("button", { name: /^deploy$/i });
    fireEvent.click(deploy);

    const said = await screen.findByText(/deploy failed/i);
    expect(said).toHaveTextContent(/does not open/i);
    expect(said).toHaveTextContent(/index\.html/);
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("does not say Deployed when the build passed but left nothing to open", async () => {
    // exit 0, and `dist/index.html` still is not there (an outDir that is not
    // `dist/`, or a view file without `entry: dist/index.html`).
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(sse({ type: "done", exit_code: 0 }), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      })),
    );
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="rca">
          <FileServiceProvider value={svc({ "/sales/package.json": BUILT["/sales/package.json"] })}>
            <WuiView
              path="/sales/page.ai.yaml"
              spec={{ view: "wui", entity: "", entry: "dist/index.html" } as ViewSpec}
            />
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );
    await screen.findByRole("button", { name: /^rebuild$/i });
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    const said = await screen.findByText(/deploy failed/i);
    expect(buildCalls()).toHaveLength(1);
    expect(said).toHaveTextContent(/does not open/i);
    expect(screen.queryByText(/^✓ deployed/i)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /address/i })).toBeNull();
  });

  it("reads the manifest again when pressed, so a page that gained a build after opening is built", async () => {
    /**
     * Review round 1: `wuiBuildable` is a one-shot snapshot (staleTime
     * Infinity, never invalidated). A page opened while still plain, then
     * given a Vite build by the agent, deployed as "nothing to build" — an
     * address to a folder with no `dist/`. Deploy re-reads the manifest at
     * the moment it is pressed; the cached answer is from the moment the pane
     * opened, and that is not the moment that matters.
     */
    const files: Record<string, string> = { ...PLAIN };
    const { release } = serveHeldBuild(0);
    const { fs } = renderInFs(files);
    await waitFor(() => expect(frame()).toBeInTheDocument());
    await waitFor(() => expect(fs.readFile).toHaveBeenCalledWith("/sales/package.json"));
    expect(screen.queryByRole("button", { name: /^rebuild$/i })).toBeNull();

    // The agent scaffolds a build under the open pane.
    files["/sales/package.json"] = BUILT["/sales/package.json"];
    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));

    await screen.findByText(/> vite build/);
    expect(buildCalls()).toHaveLength(1);
    release();
    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(ADDRESS);
  });

  it("waits for the manifest when pressed before it has been read, instead of taking the shortcut", async () => {
    /**
     * Found red in P2: with the manifest read still in flight, `canBuild` was
     * false and Deploy took the "nothing to build" shortcut. Re-reading on
     * press closes it by construction — the press joins the read in flight.
     */
    let answer: () => void = () => {};
    const gate = new Promise<void>((r) => (answer = r));
    const files = { ...BUILT };
    const { release } = serveHeldBuild(0);
    const { fs } = renderInFs(files, async (path, real) => {
      if (path.endsWith("package.json")) await gate;
      return real(path);
    });
    await waitFor(() => expect(frame()).toBeInTheDocument());
    expect(fs.readFile).toHaveBeenCalledWith("/sales/package.json");

    fireEvent.click(screen.getByRole("button", { name: /^deploy$/i }));
    expect(buildCalls()).toHaveLength(0); // still waiting on the manifest
    answer();

    await screen.findByText(/> vite build/);
    expect(buildCalls()).toHaveLength(1);
    release();
    expect(await screen.findByRole("textbox", { name: /address/i })).toHaveValue(ADDRESS);
  });
});
