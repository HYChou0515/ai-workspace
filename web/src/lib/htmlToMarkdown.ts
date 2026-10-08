/**
 * A pasted web page, as Markdown (docs/plan-outside-lookup.md D4): what the
 * person copied from a page outside, kept as links, tables, headings and lists
 * rather than flattened into the plain text a `<textarea>` would get.
 *
 * DOMPurify first — the clipboard's HTML is the page's, scripts and handlers
 * included — then turndown with the GFM plugin (tables, strikethrough), the
 * standard pairing for this job rather than a converter of our own.
 */
import DOMPurify from "dompurify";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

let service: TurndownService | null = null;

function converter(): TurndownService {
  if (service) return service;
  service = new TurndownService({
    headingStyle: "atx",
    codeBlockStyle: "fenced",
    bulletListMarker: "-",
  });
  service.use(gfm);
  // What is left after sanitising but never reads as text.
  service.remove(["style", "script", "noscript"]);
  return service;
}

export function htmlToMarkdown(html: string): string {
  const clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
  return converter().turndown(clean).trim();
}
