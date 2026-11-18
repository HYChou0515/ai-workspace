/**
 * Pure path utilities for the workspace file tree / breadcrumbs.
 *
 * The file-type → renderer dispatch (pickRenderer / isRawEditorView /
 * hasOutline / the renderer table) lives in `renderers/registry.ts` — the one
 * place to add a preview type. `imageMime` stays here (a pure helper the image
 * renderer uses; keeping it out of the registry avoids an import cycle).
 */

import { compareNames } from "../../lib/treeOrder";

/** MIME type for an image path, for building a Blob URL from edited bytes. */
export function imageMime(path: string): string {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  switch (ext) {
    case "png":
      return "image/png";
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "gif":
      return "image/gif";
    case "svg":
      return "image/svg+xml";
    case "webp":
      return "image/webp";
    case "bmp":
      return "image/bmp";
    default:
      return "application/octet-stream";
  }
}

/** Basename of a file path. Used in tab strip and breadcrumb. */
export function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  return i === -1 ? trimmed : trimmed.slice(i + 1);
}

/** Directory portion of a path, leading slash stripped, for the search
 * panel's dim file-location label. Root-level files return "". */
export function dirname(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  if (i <= 0) return "";
  return trimmed.slice(0, i).replace(/^\/+/, "");
}

export type DirEntry = { name: string; path: string; isDir: boolean };

/** Immediate children of directory `dir` (no leading slash, "" = root)
 * derived from a flat path list. Folders first (alpha), then files
 * (alpha). Folder `path` is the dir key (re-feedable to dirChildren);
 * file `path` is the full "/..." path (feedable to onOpenFile). */
export function dirChildren(paths: string[], dir: string): DirEntry[] {
  const prefix = dir ? `${dir}/` : "";
  const dirs = new Set<string>();
  const filesOut: DirEntry[] = [];
  for (const raw of paths) {
    const rel = raw.replace(/^\/+/, "");
    if (!rel.startsWith(prefix)) continue;
    const rest = rel.slice(prefix.length);
    if (!rest) continue;
    const slash = rest.indexOf("/");
    if (slash === -1) {
      filesOut.push({ name: rest, path: `/${rel}`, isDir: false });
    } else {
      dirs.add(rest.slice(0, slash));
    }
  }
  // The same order as the file tree (`treeOrder`, shared with the backend's
  // document walk) — the breadcrumb dropdown and the tree must not disagree
  // on where `10.png` sits relative to `9.png`.
  const dirsOut: DirEntry[] = [...dirs]
    .sort(compareNames)
    .map((name) => ({ name, path: `${prefix}${name}`, isDir: true }));
  filesOut.sort((a, b) => compareNames(a.name, b.name));
  return [...dirsOut, ...filesOut];
}

/** Path segments excluding root and basename, for breadcrumb display. */
export function breadcrumbSegments(path: string): string[] {
  const parts = path.split("/").filter((p) => p.length > 0);
  if (parts.length <= 1) return [];
  return parts.slice(0, -1);
}

/** How many characters of a folder crumb survive shortening: `crumbFloorCh`
 * turns them into the floor the crumb's box (button + chevron) is given in
 * CSS, and into what the fold rule below budgets for. */
const CRUMB_KEEP_CHARS = 5;
// The budget is in px because the bar is measured in px, and it is an
// estimate: ~7 px a character at the bar's 12 px type, plus 28 px around it —
// the 22 px the CSS floor adds (button padding 12, chevron 10), the bar's gap,
// and a little slack so the fold comes slightly early rather than late.
const CRUMB_CHAR_PX = 7;
const CRUMB_CHROME_PX = 28;

/**
 * The floor of a folder crumb's label, in `ch`: its first `CRUMB_KEEP_CHARS`
 * characters. `ch` is the width of "0" and a CJK character is about two of
 * them — counted as one, 「專案」 got a floor narrower than itself and was cut
 * to 「專…」 beside a file name that had room to spare.
 */
export function crumbFloorCh(name: string): number {
  let ch = 0;
  for (const c of Array.from(name).slice(0, CRUMB_KEEP_CHARS)) {
    // CJK radicals onward (U+2E80…): ideographs, kana, hangul, full-width forms.
    ch += (c.codePointAt(0) ?? 0) >= 0x2e80 ? 2 : 1;
  }
  return ch;
}
/** Room the file name keeps before folders start folding away. */
const CRUMB_FILE_MIN_PX = 160;
const CRUMB_BAR_PADDING_PX = 24;

/**
 * How many LEADING folders of the path bar fold into one "…".
 *
 * The bar is one line (NN/g, Breadcrumbs guideline 9: a trail that wraps no
 * longer shows the chain; guideline 11: shorten it to the last levels where
 * room is short). Folders shorten first, each down to `CRUMB_KEEP_CHARS`; only
 * when the bar cannot hold even the shortened folders beside a readable file
 * name do they fold — all but the file's own folder. It is the same two-state
 * shape as the global nav's trail ("…" opening a popover), with two
 * differences: that one keeps only its last crumb and folds on the shell's
 * narrow breakpoint; this one keeps the parent folder too and folds on the
 * bar's own measured width. `width` 0 is "not measured" and folds nothing.
 */
export function foldedCrumbCount(width: number, folders: string[]): number {
  if (!width || folders.length < 2) return 0;
  const shortened = folders.reduce(
    (px, name) => px + crumbFloorCh(name) * CRUMB_CHAR_PX + CRUMB_CHROME_PX,
    0,
  );
  const needed = shortened + CRUMB_FILE_MIN_PX + CRUMB_BAR_PADDING_PX;
  return width < needed ? folders.length - 1 : 0;
}
