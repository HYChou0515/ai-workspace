/**
 * Bind `assembleWuiDoc`'s `load` to a workspace folder.
 *
 * This is where the WUI's folder stops being a naming convention and starts
 * being a boundary: a reference that does not resolve inside the folder never
 * reaches the FileService at all. Filtering the RESULT would be a different,
 * weaker thing — the read would still have happened.
 */

import type { FileService } from "../../api/fileService";
import { assembleWuiDoc, type WuiAsset, type WuiDoc, type WuiLoad } from "./assemble";
import { resolveInFolder } from "./paths";

/** A blob as a `data:` URL — the only form an image can take in a document that
 * has no network. */
function toDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("could not read blob"));
    reader.readAsDataURL(blob);
  });
}

/**
 * What to call these bytes in a `data:` URL.
 *
 * The browser will not render an image, play a video or load a font it has been
 * handed as `application/octet-stream`, and the FileService reports no type —
 * it deals in bytes. The extension is the only thing left, which is fine: it is
 * also what the author wrote in the tag.
 *
 * SVG is deliberately absent — it decodes as UTF-8 and is inlined as text.
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

function mediaType(path: string): string {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  // Unknown still resolves: a generic type beats a reference the CSP refuses.
  return MEDIA_TYPES[ext] ?? "application/octet-stream";
}

/**
 * Read files beside the WUI's view file.
 *
 * Anything that cannot be produced — outside the folder, missing, or a binary
 * whose bytes will not come — resolves to `null`, because the assembler's answer
 * to "no asset" is to leave the reference alone so CSP refuses it visibly. An
 * exception here would instead take down the whole render, turning one missing
 * image into a blank page.
 */
export function folderLoader(fs: FileService, folder: string): WuiLoad {
  return async (rel: string): Promise<WuiAsset | null> => {
    const path = resolveInFolder(folder, rel);
    return path === null ? null : readAsset(fs, path);
  };
}

/**
 * One workspace file in the shape a page can hold: text as text, anything else
 * as a `data:` URL, because a null-origin frame with no network cannot follow a
 * URL to fetch the bytes itself.
 *
 * `null` for anything that will not come. Every caller's answer to a missing
 * file is to carry on — the assembler leaves the reference for CSP to refuse by
 * name, and the bridge turns it into a sentence — so throwing here would only
 * convert one absent image into a blank pane.
 */
export async function readAsset(fs: FileService, path: string): Promise<WuiAsset | null> {
  let content;
  try {
    content = await fs.readFile(path);
  } catch {
    return null;
  }

  if (content.kind === "text") {
    // The workspace service returns `kind: "text"` for EVERY file — it decodes
    // the bytes and flags the ones that are not UTF-8 as `binary` (latin1, one
    // char per byte, reversible) so that anything can be opened in the editor.
    // So this flag, not `kind`, is what says "this is an image": keying on
    // `kind` alone left every picture in every WUI a broken reference in
    // production while the tests, whose double emitted the other shape, agreed
    // it worked.
    //
    // The bytes are already here, so there is nothing to fetch: `btoa` over a
    // latin1 string is exactly base64 of those bytes.
    if (content.encoding === "binary") {
      return { kind: "binary", dataUrl: `data:${mediaType(path)};base64,${btoa(content.text)}` };
    }
    return { kind: "text", text: content.text };
  }

  // A service that reports binary directly (the in-memory one) says a file
  // exists but not what is in it; the raw route is where those bytes live.
  try {
    const resp = await fetch(fs.fileDownloadUrl(path));
    if (!resp.ok) return null;
    return { kind: "binary", dataUrl: await toDataUrl(await resp.blob()) };
  } catch {
    return null;
  }
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
export async function buildWuiDoc(
  fs: FileService,
  folder: string,
  entry: string,
): Promise<WuiDoc> {
  const load = folderLoader(fs, folder);
  const doc = await load(entry);
  if (doc?.kind !== "text") throw new WuiEntryMissing(entry);
  const built = await assembleWuiDoc(doc.text, load);
  // The entry is code by definition; `assembleWuiDoc` only sees what it pulls IN.
  return { ...built, used: [entry, ...built.used] };
}
