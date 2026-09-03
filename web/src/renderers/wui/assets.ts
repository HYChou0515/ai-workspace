/**
 * Bind `assembleWuiDoc`'s `load` to a workspace folder.
 *
 * This is where the WUI's folder stops being a naming convention and starts
 * being a boundary: a reference that does not resolve inside the folder never
 * reaches the FileService at all. Filtering the RESULT would be a different,
 * weaker thing — the read would still have happened.
 */

import { HttpError } from "../../api/http";
import type { FileService } from "../../api/fileService";
import { assembleWuiDoc, type WuiAsset, type WuiDoc, type WuiLoad } from "./assemble";
import { resolveInFolder } from "./paths";

/**
 * What a file is FOR, keyed by extension.
 *
 * Deliberately not keyed by whether the bytes decoded as UTF-8. That is what
 * the service reports, and it answers a different question: a Big5 `app.js` and
 * a CP950 `.csv` are "not UTF-8" and are still text, while a `.png` is a picture
 * whatever is in it. Keying on the encoding turned every legacy-encoded source
 * file in a workspace into an image — an `index.html` with one such byte failed
 * with "this WUI has no index.html", which was not true.
 *
 * SVG is here on purpose: it is text, but it is text used AS a picture, so it
 * has to survive being pointed at by `<img src>`.
 */
const MEDIA_TYPES: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  bmp: "image/bmp",
  ico: "image/x-icon",
  svg: "image/svg+xml",
  mp4: "video/mp4",
  webm: "video/webm",
  ogg: "audio/ogg",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  woff: "font/woff",
  woff2: "font/woff2",
  ttf: "font/ttf",
  otf: "font/otf",
  pdf: "application/pdf",
};

function mediaType(path: string): string | null {
  return MEDIA_TYPES[path.toLowerCase().split(".").pop() ?? ""] ?? null;
}

/**
 * Base64, in chunks.
 *
 * `String.fromCharCode(...bytes)` is the obvious spelling and throws
 * `RangeError: Maximum call stack size exceeded` somewhere past 100 000
 * arguments — measured at 125 000 in Chromium. A generated SVG chart clears
 * that easily, and the throw escaped through the render, so one oversized asset
 * replaced the whole page with a stack-overflow message. `encoding.ts` solved
 * the same problem 90 lines away and this did not.
 */
function base64(bytes: Uint8Array): string {
  const CHUNK = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** A blob as a `data:` URL, for a service that reports bytes it will not decode. */
function toDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("could not read blob"));
    reader.readAsDataURL(blob);
  });
}

/**
 * Reading a file has three outcomes, and they are not interchangeable.
 *
 * `missing` is ordinary — a page's first run reads a data file that is not there
 * yet. `failed` is not: a 403 for a read-only viewer, a 500, a dropped
 * connection. Collapsing them (which `null` did) meant every such failure was
 * reported to the page as "there is no file at X" and, once not-found stopped
 * being reported at all, silently.
 */
export type AssetRead =
  | { kind: "asset"; asset: WuiAsset }
  | { kind: "missing" }
  | { kind: "failed"; reason: string };

/** Read one workspace file in the shape a page can hold. */
export async function readAsset(fs: FileService, path: string): Promise<AssetRead> {
  let content;
  try {
    content = await fs.readFile(path);
  } catch (err) {
    if (err instanceof HttpError && err.status !== 404) {
      return { kind: "failed", reason: err.message };
    }
    return { kind: "missing" };
  }

  const type = mediaType(path);
  if (content.kind === "text") {
    // The workspace service decodes every file and returns `kind: "text"`,
    // flagging the non-UTF-8 ones as `binary` so anything can be opened in the
    // editor. Whether this is a PICTURE is the extension's answer, not that
    // flag's — see MEDIA_TYPES.
    if (type === null) return { kind: "asset", asset: { kind: "text", text: content.text } };
    const bytes =
      content.encoding === "binary"
        ? Uint8Array.from(content.text, (c) => c.charCodeAt(0) & 0xff)
        : new TextEncoder().encode(content.text);
    return { kind: "asset", asset: { kind: "binary", dataUrl: `data:${type};base64,${base64(bytes)}` } };
  }

  // A service that reports binary directly (the in-memory one) says a file
  // exists but not what is in it; the raw route is where those bytes live.
  try {
    const resp = await fetch(fs.fileDownloadUrl(path));
    if (!resp.ok) return { kind: "failed", reason: `could not read ${path} (${resp.status})` };
    return { kind: "asset", asset: { kind: "binary", dataUrl: await toDataUrl(await resp.blob()) } };
  } catch {
    return { kind: "failed", reason: `could not read ${path}` };
  }
}

/**
 * Read files beside the WUI's view file.
 *
 * Anything that cannot be produced — outside the folder, missing, or unreadable
 * — resolves to `null`, because the assembler's answer to "no asset" is to leave
 * the reference alone so the page's own error reporting names it. An exception
 * here would instead take down the whole render, turning one missing image into
 * a blank page.
 */
export function folderLoader(fs: FileService, folder: string): WuiLoad {
  return async (rel: string): Promise<WuiAsset | null> => {
    const path = resolveInFolder(folder, rel);
    if (path === null) return null;
    const read = await readAsset(fs, path);
    return read.kind === "asset" ? read.asset : null;
  };
}

/** Raised when the entry document itself is missing — the one absence that has
 * nothing to degrade to, so it is reported by name rather than swallowed. */
export class WuiEntryMissing extends Error {
  constructor(readonly entry: string) {
    super(`This WUI has no ${entry} to open.`);
    this.name = "WuiEntryMissing";
  }
}

/** Build the document for a WUI folder: read its entry, fold the folder in. */
export async function buildWuiDoc(fs: FileService, folder: string, entry: string): Promise<WuiDoc> {
  const load = folderLoader(fs, folder);
  const doc = await load(entry);
  if (doc?.kind !== "text") throw new WuiEntryMissing(entry);
  const built = await assembleWuiDoc(doc.text, load);
  // The entry is code by definition; `assembleWuiDoc` only sees what it pulls IN.
  return { ...built, used: [entry, ...built.used] };
}
