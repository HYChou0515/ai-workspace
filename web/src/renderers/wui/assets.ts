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

/**
 * Extensions that are text however they are encoded.
 *
 * The whole point of the table above is that a Big5 `app.js` is a script, not a
 * picture. But "not in the media table" cannot mean "text" on its own — that
 * left `photo.jfif` (what Windows writes when you save a JPEG), `.tif`, `.heic`
 * and an agent's own `chart.bin` as bare references the CSP then refused. So
 * the two named lists decide what they know, and an extension in NEITHER falls
 * back to what the bytes say.
 */
const TEXT_EXTENSIONS = new Set([
  "js", "mjs", "cjs", "jsx", "ts", "tsx", "css", "html", "htm", "json", "jsonl",
  "md", "markdown", "txt", "csv", "tsv", "yaml", "yml", "xml", "toml", "ini",
  "cfg", "conf", "log", "sql", "py", "sh",
]);

/** `null` ⇒ text; a string ⇒ this is data, under that media type. */
function classify(path: string, encoding: string): string | null {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  if (MEDIA_TYPES[ext]) return MEDIA_TYPES[ext];
  if (TEXT_EXTENSIONS.has(ext)) return null;
  // Unknown extension: the bytes decide. Valid UTF-8 is text; anything else is
  // data, and a generic type still renders — a browser content-sniffs an
  // `<img>`, so a JPEG named `.jfif` works. (Not for `<video>`, which needs a
  // real type — but an out-of-table video never worked, so nothing is lost.)
  return encoding === "binary" ? "application/octet-stream" : null;
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
    // Only the workspace service throws a typed error, and only it can tell
    // "not there" from "not allowed": a 404 is absence, any other status is a
    // fault worth showing. A `TypeError` is `fetch` failing to complete at all
    // — a dropped connection — which is emphatically not absence and used to be
    // filed as one.
    //
    // Everything else falls to absence, and that is a WEAKER answer than it
    // looks: `kbFileService` throws a plain `Error` for ANY non-ok status, so a
    // KB 403 lands here as "not there". Fixing that means giving those services
    // a typed failure of their own, which is theirs to do — this is where it
    // would be read, not where it can be decided.
    if (err instanceof HttpError) {
      if (err.status === 404) return { kind: "missing" };
      // Not `err.message`: that is "read /w/index.html failed: 403", an
      // internal path and a bare number shown to someone who cannot open a
      // console. True, and not a sentence they can act on.
      return {
        kind: "failed",
        reason:
          err.status === 403
            ? `You do not have permission to read ${path}.`
            : `${path} could not be read (the workspace answered ${err.status}).`,
      };
    }
    if (err instanceof TypeError) {
      return { kind: "failed", reason: `Could not reach the workspace to read ${path}.` };
    }
    return { kind: "missing" };
  }

  if (content.kind === "text") {
    const type = classify(path, content.encoding);
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

/** Raised when the entry document itself cannot be opened — the one absence that
 * has nothing to degrade to, so it is reported by name rather than swallowed. */
export class WuiEntryMissing extends Error {
  constructor(readonly entry: string, reason?: string) {
    // The reason matters: telling a read-only viewer their page "has no
    // index.html" is a false sentence about a file they can see in the tree,
    // and it sends them looking for the wrong thing.
    super(reason ?? `This WUI has no ${entry} to open.`);
    this.name = "WuiEntryMissing";
  }
}

/** Build the document for a WUI folder: read its entry, fold the folder in. */
export async function buildWuiDoc(fs: FileService, folder: string, entry: string): Promise<WuiDoc> {
  const load = folderLoader(fs, folder);
  // Read the entry through the three-outcome reader rather than the loader,
  // which collapses them: an entry that exists but could not be READ is not the
  // same as one that is not there, and this is the one place a person is told.
  const path = resolveInFolder(folder, entry);
  const read = path === null ? ({ kind: "missing" } as const) : await readAsset(fs, path);
  if (read.kind === "failed") throw new WuiEntryMissing(entry, read.reason);
  if (read.kind === "missing") throw new WuiEntryMissing(entry);
  if (read.asset.kind !== "text") {
    // It IS there — saying it is not sends them looking for the wrong thing,
    // which is the same false-sentence class the `failed` branch above exists
    // to remove.
    throw new WuiEntryMissing(entry, `${entry} is not a page this can open — a WUI's entry is HTML.`);
  }
  const built = await assembleWuiDoc(read.asset.text, load);
  // The entry is code by definition; `assembleWuiDoc` only sees what it pulls IN.
  return { ...built, used: [entry, ...built.used] };
}
