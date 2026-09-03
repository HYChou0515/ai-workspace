// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";

import { WUI_CSP, assembleWuiDoc } from "./assemble";

/** A loader over a fixed `{path: text}` map; anything else resolves to null. */
const textLoader = (files: Record<string, string>) =>
  vi.fn(async (rel: string) =>
    rel in files ? ({ kind: "text", text: files[rel] } as const) : null,
  );

describe("assembleWuiDoc", () => {
  it("injects the CSP as the head's first child, so nothing can widen it", async () => {
    const { doc } = await assembleWuiDoc("<html><head><title>x</title></head><body>hi</body></html>", textLoader({}));

    const parsed = new DOMParser().parseFromString(doc, "text/html");
    const first = parsed.head.firstElementChild;
    expect(first?.tagName).toBe("META");
    expect(first?.getAttribute("http-equiv")).toBe("Content-Security-Policy");
    expect(first?.getAttribute("content")).toBe(WUI_CSP);
  });

  it("blocks the network but allows inline script and style", () => {
    // The page is one self-contained srcDoc, so inline is the ONLY way its code
    // can run; the thing being taken away is reaching out, not running.
    expect(WUI_CSP).toContain("default-src 'none'");
    expect(WUI_CSP).toContain("script-src 'unsafe-inline'");
    expect(WUI_CSP).toContain("style-src 'unsafe-inline'");
    // No connect-src of its own ⇒ it falls back to default-src 'none': a page
    // cannot fetch/XHR/WebSocket anywhere, which is what keeps workspace bytes
    // from leaving through a null origin (CORS would not stop the request).
    expect(WUI_CSP).not.toContain("connect-src");
  });

  it("inlines a relative <script src> so the page needs no network", async () => {
    const load = textLoader({ "app.js": "console.log(1)" });
    const { doc } = await assembleWuiDoc(
      `<html><head></head><body><script src="./app.js"></script></body></html>`,
      load,
    );

    expect(load).toHaveBeenCalledWith("app.js");
    const parsed = new DOMParser().parseFromString(doc, "text/html");
    const script = parsed.querySelector("body script");
    expect(script?.hasAttribute("src")).toBe(false);
    expect(script?.textContent).toBe("console.log(1)");
  });

  it("inlines a relative stylesheet <link> as a <style>", async () => {
    const load = textLoader({ "style.css": "body{color:red}" });
    const { doc } = await assembleWuiDoc(
      `<html><head><link rel="stylesheet" href="style.css"></head><body></body></html>`,
      load,
    );

    expect(load).toHaveBeenCalledWith("style.css");
    const parsed = new DOMParser().parseFromString(doc, "text/html");
    expect(parsed.querySelector("link")).toBeNull();
    expect(parsed.querySelector("head style")?.textContent).toBe("body{color:red}");
  });

  it("reports which files it consumed, so the caller knows what counts as code", async () => {
    const load = textLoader({ "app.js": "1", "style.css": "2" });
    const { used } = await assembleWuiDoc(
      `<html><head><link rel="stylesheet" href="style.css"></head><body><script src="app.js"></script></body></html>`,
      load,
    );

    expect(used.sort()).toEqual(["app.js", "style.css"]);
  });

  it("leaves an absolute or external ref alone — CSP refuses it and the page says so", async () => {
    const load = textLoader({});
    const { doc, used } = await assembleWuiDoc(
      `<html><head></head><body><script src="https://cdn.example/x.js"></script></body></html>`,
      load,
    );

    expect(load).not.toHaveBeenCalled();
    expect(used).toEqual([]);
    expect(doc).toContain("https://cdn.example/x.js");
  });

  it("leaves a ref it cannot resolve alone rather than emptying the tag", async () => {
    // Silently replacing a missing file with an empty script would hide the
    // mistake; left as-is, CSP blocks it and the page's error report names it.
    const load = textLoader({});
    const { doc } = await assembleWuiDoc(
      `<html><head></head><body><script src="./missing.js"></script></body></html>`,
      load,
    );

    const parsed = new DOMParser().parseFromString(doc, "text/html");
    expect(parsed.querySelector("body script")?.getAttribute("src")).toBe("./missing.js");
  });

  it("inlines a relative <img src> as a data URL", async () => {
    const load = vi.fn(async (rel: string) =>
      rel === "logo.png" ? ({ kind: "binary", dataUrl: "data:image/png;base64,AA" } as const) : null,
    );
    const { doc, used } = await assembleWuiDoc(
      `<html><head></head><body><img src="./logo.png"></body></html>`,
      load,
    );

    expect(used).toEqual(["logo.png"]);
    const parsed = new DOMParser().parseFromString(doc, "text/html");
    expect(parsed.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,AA");
  });

  it("keeps inlined JS from closing its own tag", async () => {
    // A serialiser does not escape a script element's text, so a file that
    // merely MENTIONS the closing sequence would spill the rest of itself into
    // the page as markup. The escape is invisible to the JS parser.
    const load = textLoader({ "app.js": `const s = "</script><h1>escaped</h1>";` });
    const { doc } = await assembleWuiDoc(
      `<html><head></head><body><script src="app.js"></script></body></html>`,
      load,
    );

    const parsed = new DOMParser().parseFromString(doc, "text/html");
    expect(parsed.querySelector("h1")).toBeNull();
    expect(parsed.querySelector("script")?.textContent).toContain(String.raw`<\/script>`);
  });
});
