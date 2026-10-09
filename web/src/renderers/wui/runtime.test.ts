// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";

import { WUI_RUNTIME_SOURCE } from "./runtime";

type Handler = (ev: unknown) => void;

/** A window of our own, so the runtime's listeners cannot leak between tests
 * and each case starts from nothing. */
function boot(extra: Record<string, unknown> = {}, prepare?: (win: object) => void) {
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
    ...extra,
  } as Record<string, unknown> & { addEventListener: (t: string, f: Handler, c?: boolean) => void };
  const parent = { postMessage: (m: Record<string, unknown>) => sent.push(m) };

  prepare?.(win);
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
  return { sent, fire, fireCapture, ws, win };
}

afterEach(() => {
  document.body.innerHTML = "";
  document.body.style.cursor = "";
  vi.restoreAllMocks();
});

describe("the WUI runtime", () => {
  it("serialises to a self-contained function expression", () => {
    // It used to be a `String.raw` template, and a backtick in a comment closed
    // it and stopped the module parsing — three times. It is a real function
    // now, so that hazard is gone and the code is type-checked; the hazard that
    // REPLACES it is referencing something outside the function, which
    // serialisation silently drops. `boot()` below runs the serialised text
    // through `new Function`, so every other test in this file is that guard —
    // this one just states it.
    expect(WUI_RUNTIME_SOURCE.trimStart()).toMatch(/^function\b/);
    expect(() => new Function(`return (${WUI_RUNTIME_SOURCE})`)()).not.toThrow();
  });

  it("keeps a run's call open while its progress arrives", async () => {
    /**
     * The whole reason `startRun` exists. Progress events arrive under the SAME
     * id as the call, many of them, long before the one answer — so delivering
     * one must NOT settle the promise.
     *
     * Untested, a mutation that treated a progress event like the answer passed
     * everything: the page would resolve on the first "reading 12 files", show a
     * result that is not one, and never hear the rest.
     */
    const { sent, fire, ws } = boot();
    const seen: unknown[] = [];
    let settled = false;

    const call = ws()
      .startRun("judge", { lot: "A1" }, (e: unknown) => seen.push(e))
      .then((v: unknown) => {
        settled = true;
        return v;
      });

    const id = String(sent[0].id);

    fire("message", { data: { proto: "wui/1", id, event: "run_event", payload: { step: 1 } } });
    fire("message", { data: { proto: "wui/1", id, event: "run_event", payload: { step: 2 } } });
    await Promise.resolve();

    expect(seen).toEqual([{ step: 1 }, { step: 2 }]);
    expect(settled).toBe(false); // still running — this is the point

    // A bare `ok` reply, which is what the parent actually sends for `startRun`.
    // A `{run_id}` here was a shape the platform never produces — the same
    // double-invents-the-contract mistake the example was written against.
    fire("message", { data: { proto: "wui/1", id, ok: true, value: undefined } });

    // Resolves with nothing. What the test is for is that it resolves HERE —
    // at the reply — and not at the first progress event.
    await expect(call).resolves.toBeUndefined();
  });

  it("a throwing progress handler is reported, not swallowed", async () => {
    /**
     * The handler is the page author's code, and a page is written by an LLM. A
     * throw here must reach the pane the way any other page error does — silence
     * would leave a run that IS progressing looking frozen.
     */
    const { sent, fire, ws } = boot();

    void ws().startRun("judge", {}, () => {
      throw new Error("bad draw");
    });
    const id = String(sent[0].id);

    fire("message", { data: { proto: "wui/1", id, event: "run_event", payload: {} } });

    expect(sent.some((m) => m.report === "error" && String(m.message).includes("bad draw"))).toBe(
      true,
    );
  });

  it("gives the page these verbs and nothing else", () => {
    // The set is closed to CAPABILITIES: another name that reaches an outside
    // system would be one nobody reviewed, and the page would have found it
    // before we did. Those arrive as `callTool` targets, never as a verb.
    //
    // `startRun` is the exception, and it is one on purpose (#WUI P18): a run is
    // the platform's own execution primitive, in the same class as `readFile`,
    // and it CANNOT be a `callTool` target because `callTool` answers once and a
    // run reports progress for minutes. Adding it as a verb is a deliberate
    // widening of the trusted surface; adding the next one needs the same
    // argument, in writing, or the rule has quietly become nothing.
    const { ws } = boot();

    expect(Object.keys(ws()).sort()).toEqual([
      "callTool",
      "deleteFile",
      "listFiles",
      "onFileChanged", // a subscription, not a verb
      "openFile",
      // `plan-wui-viewer-login`: the second primitive. It cannot be a
      // `callTool` target because its whole point is that the credential NEVER
      // passes through the page — the platform opens its own sign-in, drawn
      // outside the frame, and the page only asks for it to be shown.
      "openLogin",
      "readFile",
      "startRun",
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

  it("reports one line per blocked URL however long the URL is", () => {
    // A blocked ref announces itself twice (the element error and the policy
    // violation). Skipping the dedup for long keys brought that back for any
    // ordinary long URL — a signed CDN link is exactly that shape.
    const long = "https://cdn.example/" + "a".repeat(400) + ".png";
    const { sent, fire, fireCapture } = boot();

    fireCapture("error", { target: Object.assign(document.createElement("img"), { src: long }) });
    fire("securitypolicyviolation", { violatedDirective: "img-src", blockedURI: long });

    expect(sent.filter((m) => m.report === "error")).toHaveLength(1);
  });

  it("still reports every blocked data: URL, which share one useless key", () => {
    // Chromium reports them all as the bare string "data", so deduping on it
    // silences every one after the first and names none of them.
    const { sent, fire } = boot();

    fire("securitypolicyviolation", { violatedDirective: "img-src", blockedURI: "data" });
    fire("securitypolicyviolation", { violatedDirective: "script-src-elem", blockedURI: "data" });

    expect(sent.filter((m) => m.report === "error")).toHaveLength(2);
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


/** A click as the window's bubble listener receives it. */
function click(target: Element, init: Partial<{ defaultPrevented: boolean; button: number; ctrlKey: boolean }> = {}) {
  const ev = {
    target,
    defaultPrevented: init.defaultPrevented ?? false,
    button: init.button ?? 0,
    ctrlKey: init.ctrlKey ?? false,
    metaKey: false,
    shiftKey: false,
    altKey: false,
    preventDefault: vi.fn(),
  };
  return ev;
}

function link(href: string): HTMLAnchorElement {
  const a = document.createElement("a");
  a.setAttribute("href", href);
  a.textContent = "go";
  document.body.appendChild(a);
  return a;
}

const SERVED = "http://app.test/api/wui-content/TOKEN/docs/site/index.html";

describe("the WUI runtime on a served page (plan-wui-multipage)", () => {
  it("does not let a late answer meant for the previous page settle this page's call", async () => {
    /**
     * A served site navigates its frame, and the parent's answers go to
     * whatever document is in it NOW. Ids that restart at 1 on every page meant
     * the previous page's late answer to its call "1" settled this page's call
     * "1" — with the wrong value.
     */
    const { sent, fire, ws } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });
    let settled = false;
    void ws().whoami().then(() => (settled = true));

    fire("message", { data: { proto: "wui/1", id: "1", ok: true, value: { user: "someone else" } } });
    await Promise.resolve();
    expect(settled).toBe(false);

    fire("message", { data: { proto: "wui/1", id: String(sent.at(-1)!.id), ok: true, value: { user: "me" } } });
    await Promise.resolve();
    expect(settled).toBe(true);
  });

  it("announces the page it landed on, so the address can follow it", () => {
    const { sent } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });

    expect(sent).toContainEqual({ proto: "wui/1", page: SERVED });
  });

  it("announces nothing in a single-page document, which has no address of its own", () => {
    const { sent } = boot({ location: { href: "about:srcdoc", origin: "null", protocol: "about:" } });

    expect(sent.filter((m) => "page" in m)).toEqual([]);
  });

  it("hands a link to another site to the platform instead of following it", () => {
    const { sent, fire } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });
    const ev = click(link("https://example.com/x?y=1"));

    fire("click", ev);

    expect(ev.preventDefault).toHaveBeenCalled();
    expect(sent).toContainEqual({ proto: "wui/1", open: "https://example.com/x?y=1" });
  });

  it("leaves a link within the site to the browser", () => {
    const { sent, fire } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });
    const ev = click(link("../setup/"));

    fire("click", ev);

    expect(ev.preventDefault).not.toHaveBeenCalled();
    expect(sent.filter((m) => "open" in m)).toEqual([]);
  });

  it("leaves a link alone when the page already handled the click itself", () => {
    const { sent, fire } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });

    fire("click", click(link("https://example.com/"), { defaultPrevented: true }));

    expect(sent.filter((m) => "open" in m)).toEqual([]);
  });

  it("finds the link a click landed inside of", () => {
    const { sent, fire } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });
    const a = link("https://example.com/deep");
    const inner = document.createElement("span");
    a.appendChild(inner);

    fire("click", click(inner));

    expect(sent).toContainEqual({ proto: "wui/1", open: "https://example.com/deep" });
  });

  it("routes window.open through the platform too", () => {
    const { sent, win } = boot({ location: { href: SERVED, origin: "http://app.test", protocol: "http:" } });

    const opened = (win.open as (u: string) => unknown)("https://example.com/w");

    expect(opened).toBeNull();
    expect(sent).toContainEqual({ proto: "wui/1", open: "https://example.com/w" });
  });

  it("gives a page whose storage is forbidden one that works for the visit", () => {
    /** An opaque origin's `localStorage` throws `SecurityError` (Phase 1); a
     * generator's theme toggle calls it and stopped there. */
    const forbid = (win: object) => {
      for (const name of ["localStorage", "sessionStorage"]) {
        Object.defineProperty(win, name, {
          configurable: true,
          get() {
            throw new DOMException("denied", "SecurityError");
          },
        });
      }
    };
    const { win: shimmed } = boot(
      { location: { href: SERVED, origin: "http://app.test", protocol: "http:" } },
      forbid,
    );

    const ls = shimmed.localStorage as Storage;
    ls.setItem("k", "v");
    expect(ls.getItem("k")).toBe("v");
    expect(ls.getItem("missing")).toBeNull();
    expect((shimmed.sessionStorage as Storage).getItem("k")).toBeNull();
  });

  it("starts a worker from the page's own script, in a blob that still resolves against the script", async () => {
    /** An opaque origin may not start a worker from a URL (Phase 1) — so the
     * runtime reads the script and starts it from a blob, telling it where it
     * really lives so its own importScripts / fetch resolve. */
    const made: { url: string; text: string; posted: unknown[] }[] = [];
    class RealWorker {
      onmessage: ((e: unknown) => void) | null = null;
      posted: unknown[] = [];
      constructor(url: string) {
        made.push({ url, text: blobs[url], posted: this.posted });
      }
      postMessage(m: unknown) {
        this.posted.push(m);
      }
      addEventListener() {}
      terminate() {}
    }
    const blobs: Record<string, string> = {};
    let n = 0;
    const { win } = boot({
      location: { href: SERVED, origin: "http://app.test", protocol: "http:" },
      Worker: RealWorker,
      fetch: async (url: string) => ({ ok: true, text: async () => `/*worker at ${url}*/` }),
      Blob: class {
        constructor(readonly parts: string[]) {}
      },
      URL: Object.assign(URL, {}),
    });
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockImplementation((b: unknown) => {
      const id = `blob:null/${++n}`;
      blobs[id] = (b as { parts: string[] }).parts.join("");
      return id;
    });

    const w = new (win.Worker as new (u: string) => { postMessage(m: unknown): void })("../assets/w.js");
    w.postMessage({ type: "setup" });
    await new Promise((r) => setTimeout(r, 0));

    expect(createObjectURL).toHaveBeenCalled();
    expect(made).toHaveLength(1);
    // The prologue names where the script really lives.
    expect(made[0].text).toContain('"http://app.test/api/wui-content/TOKEN/docs/assets/w.js"');
    expect(made[0].text).toContain("/*worker at http://app.test/api/wui-content/TOKEN/docs/assets/w.js*/");
    // Posted before the blob existed, delivered once it did.
    expect(made[0].posted).toEqual([{ type: "setup" }]);
  });
});

describe("the WUI runtime in a single-page document (the fallback)", () => {
  const SRCDOC = { location: { href: "about:srcdoc", origin: "null", protocol: "about:" } };

  it("scrolls to an in-page anchor instead of navigating the frame away", () => {
    /** A srcdoc document's links resolve against the PARENT's address, so
     * `#bottom` sent the frame to the platform's own app (Phase 1). */
    const { fire } = boot(SRCDOC);
    const target = document.createElement("p");
    target.id = "bottom";
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);
    const ev = click(link("#bottom"));

    fire("click", ev);

    expect(ev.preventDefault).toHaveBeenCalled();
    expect(target.scrollIntoView).toHaveBeenCalled();
  });

  it("says why a link to another page goes nowhere, rather than breaking the frame", () => {
    const { sent, fire } = boot(SRCDOC);
    const ev = click(link("report.html"));

    fire("click", ev);

    expect(ev.preventDefault).toHaveBeenCalled();
    const said = sent.find((m) => m.report === "error");
    expect(String(said?.message)).toContain("report.html");
  });

  it("still hands a link to another site to the platform", () => {
    const { sent, fire } = boot(SRCDOC);

    fire("click", click(link("https://example.com/")));

    expect(sent).toContainEqual({ proto: "wui/1", open: "https://example.com/" });
  });
});
