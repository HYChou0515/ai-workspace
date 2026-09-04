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
    const toggle = await screen.findByLabelText(/rebuild this page whenever you open it/i);

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
