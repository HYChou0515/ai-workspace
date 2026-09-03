/**
 * Assemble a WUI folder into the ONE self-contained document its iframe runs.
 *
 * A WUI is a folder of ordinary files (`index.html` + `app.js` + `style.css` +
 * images). The iframe that runs it has a null origin and no network, so the
 * browser can fetch none of those siblings itself — this module reads them
 * through the caller's `load` and folds them into the entry document.
 *
 * That is also what makes the folder the unit: the author edits three small
 * files (and an agent edits ONE of them), while the runtime still sees a single
 * page. Inlining at render time is what buys both.
 *
 * Pure: no fetch, no React, no FileService. `load` is the only way out, and it
 * is handed folder-relative paths — resolving those against the workspace is
 * the caller's business.
 */

import { wuiRuntimeScript } from "./runtime";

/** One sibling file, in the shape the tag that references it needs. */
export type WuiAsset =
  | { kind: "text"; text: string }
  | { kind: "binary"; dataUrl: string };

/** Read a file NEXT TO the entry document. `null` ⇒ there is no such file. */
export type WuiLoad = (relPath: string) => Promise<WuiAsset | null>;

/**
 * The page's ceiling. `default-src 'none'` is the load-bearing clause: with no
 * `connect-src` of its own it also governs fetch / XHR / WebSocket, so a page
 * cannot send what it read anywhere. That matters more than it looks — the
 * iframe's origin is `null`, and CORS would still let the REQUEST leave (it only
 * withholds the response), so "can't read the answer" is not "can't exfiltrate".
 *
 * Inline script and style are allowed because the whole page is one inlined
 * document: forbidding them would leave nothing able to run. What is taken away
 * is reaching out, not running.
 *
 * A CSP can only be tightened by a later policy, never widened — so injecting
 * this as the head's FIRST child puts it beyond the reach of the page's own
 * content, including a `<meta>` an agent might emit.
 */
export const WUI_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "img-src data:",
  "font-src data:",
  "media-src data:",
  "form-action 'none'",
  "base-uri 'none'",
].join("; ");

/** Refs we resolve against the folder. Anything with a scheme (`https:`,
 * `data:`), protocol-relative (`//`), root-relative (`/`) or a bare fragment is
 * left exactly as written: it is not a sibling file, so there is nothing to
 * inline, and CSP will refuse it loudly rather than us guessing. */
function folderRelative(ref: string | null): string | null {
  if (!ref) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(ref)) return null; // https: data: mailto:
  if (ref.startsWith("//") || ref.startsWith("/") || ref.startsWith("#")) return null;
  return ref.replace(/^\.\//, "");
}

/**
 * Neutralise a `</script` inside inlined JavaScript.
 *
 * The document is serialised back to a string, and an HTML serialiser does NOT
 * escape a script element's text — so a file containing that sequence anywhere
 * (a string literal, a regex, a comment) would close its own tag and spill the
 * rest of the program into the page as markup. The escape is invisible to the
 * JS parser, which reads `<\/` as `</`.
 */
function safeScriptText(code: string): string {
  return code.replace(/<\/script/gi, "<\\/script");
}

/** The assembled document plus the folder files it consumed. */
export type WuiDoc = {
  /** Ready for an iframe's `srcDoc`. */
  doc: string;
  /** Folder-relative paths that were inlined — the entry's own dependencies. */
  used: string[];
};

/**
 * Fold `entryHtml`'s folder-relative references into one document and put the
 * CSP at the top of its head.
 *
 * A reference that does not resolve is left untouched on purpose. Replacing a
 * missing `app.js` with an empty script would produce a page that renders and
 * does nothing — the failure mode with no error message. Left alone, CSP
 * refuses it and the page's own error reporting names the file.
 */
export async function assembleWuiDoc(entryHtml: string, load: WuiLoad): Promise<WuiDoc> {
  const doc = new DOMParser().parseFromString(entryHtml, "text/html");
  const used: string[] = [];

  const take = async (rel: string): Promise<WuiAsset | null> => {
    const asset = await load(rel);
    if (asset) used.push(rel);
    return asset;
  };

  for (const el of Array.from(doc.querySelectorAll("script[src]"))) {
    const rel = folderRelative(el.getAttribute("src"));
    if (!rel) continue;
    const asset = await take(rel);
    if (asset?.kind !== "text") continue;
    el.removeAttribute("src");
    el.textContent = safeScriptText(asset.text);
  }

  for (const el of Array.from(doc.querySelectorAll("link[rel~='stylesheet'][href]"))) {
    const rel = folderRelative(el.getAttribute("href"));
    if (!rel) continue;
    const asset = await take(rel);
    if (asset?.kind !== "text") continue;
    const style = doc.createElement("style");
    style.textContent = asset.text;
    el.replaceWith(style);
  }

  for (const el of Array.from(doc.querySelectorAll("img[src], source[src], video[src], audio[src]"))) {
    const rel = folderRelative(el.getAttribute("src"));
    if (!rel) continue;
    const asset = await take(rel);
    if (asset?.kind !== "binary") continue;
    el.setAttribute("src", asset.dataUrl);
  }

  // Our runtime goes second — after the CSP, which nothing may precede, and
  // before every line the agent wrote. `window.workspace` has to exist when the
  // page's first statement runs, and the error capture has to be listening
  // before the failure it exists for, which is the page failing on load.
  const runtime = doc.createElement("script");
  runtime.textContent = safeScriptText(wuiRuntimeScript());
  doc.head.insertBefore(runtime, doc.head.firstChild);

  const csp = doc.createElement("meta");
  csp.setAttribute("http-equiv", "Content-Security-Policy");
  csp.setAttribute("content", WUI_CSP);
  doc.head.insertBefore(csp, doc.head.firstChild);

  return { doc: `<!doctype html>${doc.documentElement.outerHTML}`, used };
}
