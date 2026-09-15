/**
 * How a markdown ref (`![](src)` / `[](href)`) becomes a path — the ONE rule
 * every `FileService` resolves refs by, so the same document renders the same
 * way whichever tree it was opened from.
 *
 * Kept in its own dependency-free module because both `fileService.ts` and
 * `kbFileService.ts` need it and `kbFileService` already imports `fileService`:
 * putting the helpers in `fileService.ts` would make that a value cycle.
 */

/** Normalise a path to a single leading-slash, `.`/`..`-resolved form, so doc
 * paths and resolved refs compare regardless of how they were stored. This is
 * the tree's canonical form: real uploads store relative paths (no leading
 * slash), the tree (and the investigation IDE it shares) speaks leading-slash —
 * normalising here is the single FE boundary that reconciles the two (#87). */
export function normPath(path: string): string {
  const stack: string[] = [];
  for (const seg of path.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") stack.pop();
    else stack.push(seg);
  }
  return "/" + stack.join("/");
}

/** Resolve a markdown ref against the doc it appears in: an absolute path is
 * tree-root; anything else is relative to the doc's own directory — what
 * `![](./a.png)` means in GitHub, in a VSCode preview, and to whoever wrote it.
 * `..` is resolved HERE and not left in the URL: the browser would collapse it
 * against the API route instead and walk out of the file endpoint entirely. */
export function resolveRefPath(fromPath: string, src: string): string {
  if (src.startsWith("/")) return normPath(src);
  const dir = fromPath.replace(/[^/]*$/, ""); // keep trailing slash
  return normPath(dir + src);
}

/** A ref that already addresses something outside the tree — an absolute URL,
 * a `data:` payload, a protocol-relative host, a same-page `#fragment`. These
 * are handed to the browser untouched; only tree-relative refs get resolved. */
export function isExternalRef(src: string): boolean {
  return /^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/i.test(src);
}

/** A workspace path as URL segments — the ONE spelling of that rule, for
 * everything that puts a workspace path into an address: the `/files/` routes
 * (`real.ts`), the KB leaf routes (`leafPath.ts`), asset URLs
 * (`fileService.ts`) and the WUI's own `/w/` link (`WuiView`). Four copies of
 * these three lines had accumulated by review round 7; a fix to one (a
 * literal `%`, a `.` segment) must reach them all. Here, in the
 * dependency-free path module, rather than beside the HTTP client: a
 * renderer wanting path encoding should not import `fetch`, and the client
 * module is mocked in tests that never intend to touch this. */
export function encodePath(path: string): string {
  // Drop the leading slash before joining: workspace paths reach the FE in both
  // dialects (a `shown_files` declaration normalises to absolute, a file tree row
  // is relative) and `…/files/` + `/out/a.png` would otherwise emit `files//out`.
  return path.replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/");
}
