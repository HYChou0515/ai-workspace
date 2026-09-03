// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { WUI_RUNTIME_SOURCE } from "./runtime";

type Handler = (ev: unknown) => void;

/** A window of our own, so the runtime's listeners cannot leak between tests
 * and each case starts from nothing. */
function boot() {
  const handlers: Record<string, Handler[]> = {};
  const sent: Record<string, unknown>[] = [];
  // Capture-phase listeners are kept apart, because whether the runtime
  // registers in the capture phase is the whole of what makes a failed
  // subresource visible — a bubble listener never sees one.
  const capture: Record<string, Handler[]> = {};
  const win = {
    addEventListener: (type: string, fn: Handler, useCapture?: boolean) => {
      (useCapture ? (capture[type] ??= []) : (handlers[type] ??= [])).push(fn);
    },
    getComputedStyle: (el: Element) => globalThis.getComputedStyle(el),
    workspace: undefined as unknown,
  };
  const parent = { postMessage: (m: Record<string, unknown>) => sent.push(m) };

  const make = new Function(`return (${WUI_RUNTIME_SOURCE})`)() as (
    w: unknown,
    p: unknown,
    d: Document,
  ) => void;
  make(win, parent, document);

  const fire = (type: string, ev: unknown) =>
    [...(handlers[type] ?? []), ...(capture[type] ?? [])].forEach((f) => f(ev));
  /** Only the capture-phase listeners — what a non-bubbling resource error
   * actually reaches. */
  const fireCapture = (type: string, ev: unknown) => (capture[type] ?? []).forEach((f) => f(ev));
  const ws = () => win.workspace as Record<string, (...a: unknown[]) => Promise<unknown>>;
  return { sent, fire, fireCapture, ws };
}

afterEach(() => {
  document.body.innerHTML = "";
  document.body.style.cursor = "";
  vi.restoreAllMocks();
});

describe("the WUI runtime", () => {
  it("carries no backtick, which would end the template it lives in", () => {
    // The source is a `String.raw` template, so one backtick in a comment closes
    // it and the whole module stops parsing — every test in this folder fails to
    // collect at once, which reads like something far more interesting than a
    // stray character. Made twice; pinned now.
    expect(WUI_RUNTIME_SOURCE).not.toContain("`");
  });

  it("gives the page the seven verbs and nothing else", () => {
    // The set is closed: another name here would be a capability nobody
    // reviewed, and the page would have found it before we did. Everything
    // added from now on is a `callTool` target, not an eighth verb.
    const { ws } = boot();

    expect(Object.keys(ws()).sort()).toEqual([
      "callTool",
      "deleteFile",
      "listFiles",
      "onFileChanged", // a subscription, not a verb
      "openFile",
      "readFile",
      "whoami",
      "writeFile",
    ]);
  });

  it("sends a request and resolves on the matching reply", async () => {
    const { sent, fire, ws } = boot();

    const answer = ws().readFile("data.json");
    expect(sent[0]).toMatchObject({ proto: "wui/1", verb: "readFile", args: { path: "data.json" } });

    fire("message", { data: { proto: "wui/1", id: sent[0].id, ok: true, value: { text: "[]" } } });

    await expect(answer).resolves.toEqual({ text: "[]" });
  });

  it("keeps concurrent calls apart by id", async () => {
    const { sent, fire, ws } = boot();

    const a = ws().whoami();
    const b = ws().listFiles();
    fire("message", { data: { proto: "wui/1", id: sent[1].id, ok: true, value: "second" } });
    fire("message", { data: { proto: "wui/1", id: sent[0].id, ok: true, value: "first" } });

    await expect(a).resolves.toBe("first");
    await expect(b).resolves.toBe("second");
  });

  it("rejects a refusal AND reports it, because both audiences need it", async () => {
    // The page may well catch this; the person looking at the page still has to
    // be told, and that text is what they forward to the agent.
    const { sent, fire, ws } = boot();

    const answer = ws().writeFile("/notes.md", "x");
    fire("message", { data: { proto: "wui/1", id: sent[0].id, ok: false, error: "only its own folder" } });

    await expect(answer).rejects.toThrow("only its own folder");
    expect(sent.at(-1)).toMatchObject({ report: "refused", message: "only its own folder" });
  });

  it("does not raise an alarm for a refusal the parent calls ordinary", async () => {
    // A first run reads a data file that is not there yet. Reporting that put a
    // red "not allowed" in front of every user opening every new page, which is
    // how the alarm that matters gets ignored. It still rejects.
    const { sent, fire, ws } = boot();

    const answer = ws().readFile("data.json");
    fire("message", {
      data: { proto: "wui/1", id: sent[0].id, ok: false, error: "There is no file at /a/data.json.", expected: true },
    });

    await expect(answer).rejects.toThrow("There is no file");
    expect(sent.find((m) => m.report === "refused")).toBeUndefined();
  });

  it("ignores a message that is not ours", async () => {
    const { sent, fire } = boot();

    fire("message", { data: { type: "webpack-hmr" } });
    fire("message", { data: null });

    expect(sent).toHaveLength(0);
  });

  it("reports an uncaught error with where it happened", () => {
    const { sent, fire } = boot();

    fire("error", { message: "x is not a function", filename: "app.js", lineno: 12 });

    expect(sent[0]).toMatchObject({ report: "error" });
    expect(sent[0].message).toContain("x is not a function");
    expect(sent[0].message).toContain("app.js:12");
  });

  it("reports a subresource that failed to load, naming it", () => {
    // A missing or misnamed `app.js` is left in the document on purpose, so
    // that the failure is visible rather than silently becoming an empty
    // script. It only IS visible if it reaches the pane: a resource error fires
    // on the element and does NOT bubble, so a bubble-phase listener saw
    // nothing and the page rendered, did nothing, and reported nothing —
    // exactly the outcome that choice was made to avoid.
    const { sent, fireCapture } = boot();

    fireCapture("error", {
      target: Object.assign(document.createElement("script"), { src: "http://x/app.js" }),
      message: undefined,
    });

    expect(sent[0]).toMatchObject({ report: "error" });
    expect(sent[0].message).toContain("app.js");
  });

  it("reports a CSP refusal, which is how a page reaching outward fails", () => {
    const { sent, fire } = boot();

    fire("securitypolicyviolation", {
      violatedDirective: "connect-src",
      blockedURI: "https://cdn.example/x.js",
    });

    expect(sent[0]).toMatchObject({ report: "error" });
    expect(sent[0].message).toContain("https://cdn.example/x.js");
  });

  it("reports an unhandled rejection, which is how an async page fails", () => {
    const { sent, fire } = boot();

    fire("unhandledrejection", { reason: new Error("fetch blocked") });

    expect(sent[0]).toMatchObject({ report: "error", message: "fetch blocked" });
  });

  it("passes a file_changed on to the page rather than acting on it", () => {
    const { fire, ws } = boot();
    const seen = vi.fn();
    ws().onFileChanged(seen);

    fire("message", { data: { proto: "wui/1", event: "file_changed", path: "/sales/data.json" } });

    expect(seen).toHaveBeenCalledWith("/sales/data.json");
  });

  it("survives a page handler that throws, and says that it did", () => {
    const { sent, fire, ws } = boot();
    ws().onFileChanged(() => {
      throw new Error("bad handler");
    });

    expect(() =>
      fire("message", { data: { proto: "wui/1", event: "file_changed", path: "/a" } }),
    ).not.toThrow();
    expect(sent.at(-1)).toMatchObject({ report: "error" });
  });

  it("reports what the user pointed at, with the styles a model can reason from", () => {
    // The agent cannot see the page. `outerHTML` alone does not explain "it
    // looks squashed"; the computed styles are the closest thing to looking.
    document.body.innerHTML = `<div data-wui="chart"><span id="t">42</span></div>`;
    const { sent, fire } = boot();
    fire("message", { data: { proto: "wui/1", command: "pick", on: true } });

    document.getElementById("t")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    const pick = sent.find((m) => m.report === "pick");
    expect(pick).toBeTruthy();
    const detail = pick?.detail as Record<string, unknown>;
    expect(detail.html).toContain("42");
    expect(detail.marker).toBe("chart");
    expect(detail.styles).toHaveProperty("display");
  });

  it("ignores a click whose target is not an element", () => {
    // A click can land on the document itself. This handler runs on EVERY click
    // in a page we did not write, so it must never be the thing that throws.
    const { sent, fire } = boot();
    fire("message", { data: { proto: "wui/1", command: "pick", on: true } });

    expect(() => document.dispatchEvent(new MouseEvent("click", { bubbles: true }))).not.toThrow();
    expect(sent.find((m) => m.report === "pick")).toBeUndefined();
  });

  it("does not report a click while it is not picking", () => {
    document.body.innerHTML = `<div id="t">x</div>`;
    const { sent } = boot();

    document.getElementById("t")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(sent.find((m) => m.report === "pick")).toBeUndefined();
  });

  it("stops picking after one pick, so a click is not stolen twice", () => {
    document.body.innerHTML = `<div id="t">x</div>`;
    const { sent, fire } = boot();
    fire("message", { data: { proto: "wui/1", command: "pick", on: true } });

    document.getElementById("t")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    document.getElementById("t")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(sent.filter((m) => m.report === "pick")).toHaveLength(1);
  });

  it("draws its outline with `all: initial`, out of the page's CSS reach", () => {
    // The page's stylesheet is arbitrary agent-written code, and this is the one
    // affordance that has to keep working when the page does not.
    document.body.innerHTML = `<div id="t">x</div>`;
    const { fire } = boot();
    fire("message", { data: { proto: "wui/1", command: "pick", on: true } });

    document.getElementById("t")?.dispatchEvent(new MouseEvent("mousemove", { bubbles: true }));

    const box = document.querySelector("[data-wui-pick]") as HTMLElement;
    expect(box.style.cssText).toContain("all: initial");
    expect(box.style.zIndex).toBe("2147483647");
  });
});
