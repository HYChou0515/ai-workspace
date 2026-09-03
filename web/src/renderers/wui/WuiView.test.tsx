// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import type { FileContent } from "../../api/types";
import { subscribeAgentDraft } from "../../lib/agentDraftBus";
import { publishFileChanged } from "../../lib/fileChangedBus";
import { QueryWrap } from "../../test/queryWrapper";
import type { ViewSpec } from "../entity/types";
import { WUI_CSP } from "./assemble";
import { WUI_PROTOCOL } from "./protocol";
import { WuiView } from "./WuiView";

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
