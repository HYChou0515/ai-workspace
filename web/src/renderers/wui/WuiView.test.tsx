// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService } from "../../api/fileService";
import type { FileContent } from "../../api/types";
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
    readFile: vi.fn(async (path: string) => {
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return text(path, files[path]);
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
