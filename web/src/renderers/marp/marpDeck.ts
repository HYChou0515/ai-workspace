/**
 * Pure helpers for the Marp deck renderer. No React, no DOM — just the string
 * transforms that decide whether a markdown file is a Marp deck, rewrite its
 * workspace-relative asset URLs, and size its fixed 1280×720 slides to a pane.
 */
import { load as parseYaml } from "js-yaml";

/** The leading `---\n…\n---` YAML frontmatter block, or null. Marp requires the
 *  opening fence at the very start of the file, so this anchors on byte 0. */
function frontmatterBlock(text: string): string | null {
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  return m ? m[1] : null;
}

/** Whether a markdown file is a Marp deck — i.e. its frontmatter sets the
 *  `marp: true` global directive. Faithful to the Marp ecosystem; a plain `.md`
 *  (no frontmatter, `marp: false`, or no `marp` key) is not a deck. */
export function isMarpDoc(text: string): boolean {
  const block = frontmatterBlock(text);
  if (block == null) return false;
  try {
    const doc = parseYaml(block);
    return typeof doc === "object" && doc !== null && (doc as Record<string, unknown>).marp === true;
  } catch {
    return false;
  }
}

/** A URL that must NOT be rewritten: absolute (http/protocol-relative), inline
 *  (data/blob), a fragment, or a non-navigational scheme. Everything else is
 *  treated as a workspace-relative path and sent through the resolver. */
function isExternalUrl(url: string): boolean {
  return (
    url === "" ||
    /^(?:https?:)?\/\//i.test(url) ||
    /^(?:data|blob|mailto|tel):/i.test(url) ||
    url.startsWith("#")
  );
}

function rewriteImgSrc(html: string, resolve: (src: string) => string): string {
  return html.replace(
    /(<img\b[^>]*?\bsrc=)(["'])(.*?)\2/gi,
    (whole, pre: string, _q: string, src: string) =>
      isExternalUrl(src) ? whole : `${pre}"${resolve(src)}"`,
  );
}

function rewriteCssUrls(s: string, resolve: (src: string) => string): string {
  return s.replace(
    /url\(\s*(["']?)(.*?)\1\s*\)/gi,
    (whole, _q: string, src: string) => (isExternalUrl(src) ? whole : `url("${resolve(src)}")`),
  );
}

/** Rewrite workspace-relative asset URLs in Marp's rendered output so they land
 *  on the file API. Covers `<img src>` and CSS `url()` (both in the stylesheet
 *  and in inline `style="background-image:url(…)"` attributes marp emits for
 *  `![bg]` backgrounds). External / data / fragment URLs pass through untouched. */
export function rewriteMarpAssets(
  html: string,
  css: string,
  resolve: (src: string) => string,
): { html: string; css: string } {
  return {
    html: rewriteCssUrls(rewriteImgSrc(html, resolve), resolve),
    css: rewriteCssUrls(css, resolve),
  };
}

/** The CSS transform scale that fits a native `slideWidth`px (Marp's default is
 *  1280) slide to a `paneWidth`px pane — so a fixed-size deck fills the preview
 *  width responsively (marp's own bespoke/bare templates scale the same way). */
export function slideScale(paneWidth: number, slideWidth = 1280): number {
  return paneWidth / slideWidth;
}
