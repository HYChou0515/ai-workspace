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
  if (content.kind === "text") return { kind: "text", text: content.text };

  // `readFile` reports a binary file's existence but not its bytes; the raw
  // route is where those live.
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
