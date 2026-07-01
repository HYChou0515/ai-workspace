/**
 * #364: shared parsing for files arriving via drag-drop or clipboard paste, used by
 * the chat composer (AgentPanel) and the workspace file tree (FileTree). Keeps the
 * DataTransfer / clipboard quirks — recursive folder walking, image-vs-file
 * classification, and naming a clipboard image that has no filename — in one tested
 * place so each surface's handler stays thin.
 */

/** Whether a blob is an image (→ the chat composer's preview-chip flow). */
export function isImage(file: File): boolean {
  return file.type.startsWith("image/");
}

// Clipboard images arrive with a mime but often no useful name — map the mime to a
// sensible extension so the staged workspace file is openable.
const IMAGE_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/svg+xml": "svg",
  "image/bmp": "bmp",
  "image/tiff": "tiff",
  "image/avif": "avif",
};

/** A clipboard image usually arrives nameless or as the browser default `image.png`.
 * Give it a stable, `stamp`-unique name derived from its mime so it lands as a real
 * workspace file; a blob that already carries a meaningful name is returned unchanged.
 * `stamp` is supplied by the caller (e.g. `Date.now()`) to keep this pure/testable. */
export function nameImageFile(file: File, stamp: number): File {
  const generic = !file.name || file.name === "image.png";
  if (!generic) return file;
  const ext = IMAGE_EXT[file.type] ?? "png";
  return new File([file], `pasted-image-${stamp}.${ext}`, { type: file.type });
}

export interface ClipboardHarvest {
  /** Image blobs, renamed for staging — routed to the composer's chip flow. */
  images: File[];
  /** Non-image files — routed to the existing path-injection flow. */
  files: File[];
}

/** Split a paste's payload into image blobs and other files. Prefers `items` (which
 * carry clipboard image blobs) and falls back to `files` (an OS file copy). Returns
 * empty arrays for a plain-text paste so the caller lets the text through untouched. */
export function extractClipboardFiles(dt: DataTransfer | null, stamp: number): ClipboardHarvest {
  const images: File[] = [];
  const files: File[] = [];
  if (!dt) return { images, files };

  const blobs: File[] = [];
  if (dt.items && dt.items.length) {
    for (const it of Array.from(dt.items)) {
      if (it.kind !== "file") continue;
      const f = it.getAsFile();
      if (f) blobs.push(f);
    }
  }
  if (!blobs.length && dt.files && dt.files.length) {
    blobs.push(...Array.from(dt.files));
  }

  let n = 0;
  for (const f of blobs) {
    if (isImage(f)) images.push(nameImageFile(f, stamp + n++));
    else files.push(f);
  }
  return { images, files };
}

// A trimmed view of the non-standard FileSystemEntry API (webkitGetAsEntry). Typed
// locally because lib.dom's types are patchy across engines and we only touch a few
// members.
interface FsEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (success: (f: File) => void, error?: (e: unknown) => void) => void;
  createReader?: () => {
    readEntries: (success: (entries: FsEntry[]) => void, error?: (e: unknown) => void) => void;
  };
}

/** Expand a drop's DataTransfer into a flat `File[]`, recursing into dropped folders
 * via `webkitGetAsEntry` and stamping each file's `webkitRelativePath` so the caller's
 * upload preserves the folder shape (same field a `<input webkitdirectory>` sets).
 * Falls back to the flat `dt.files` when the entries API is unavailable (older engines
 * or a synthetic transfer). */
export async function readTransferEntries(dt: DataTransfer): Promise<File[]> {
  const items = dt.items ? Array.from(dt.items) : [];
  const entries = items
    .filter((it) => it.kind === "file")
    // webkitGetAsEntry's lib.dom type (FileSystemEntry) is stricter than the subset we
    // touch; go through unknown so the local FsEntry view type-checks across engines.
    .map((it) => (it.webkitGetAsEntry?.() ?? null) as unknown as FsEntry | null)
    .filter((e): e is FsEntry => e != null);

  if (!entries.length) {
    return dt.files ? Array.from(dt.files) : [];
  }

  const out: File[] = [];
  await Promise.all(entries.map((e) => walkEntry(e, "", out)));
  return out;
}

async function walkEntry(entry: FsEntry, prefix: string, out: File[]): Promise<void> {
  if (entry.isFile && entry.file) {
    const readFile = entry.file.bind(entry);
    const file = await new Promise<File>((resolve, reject) => readFile(resolve, reject));
    const rel = prefix ? `${prefix}/${file.name}` : file.name;
    Object.defineProperty(file, "webkitRelativePath", { value: rel, configurable: true });
    out.push(file);
    return;
  }
  if (entry.isDirectory && entry.createReader) {
    const reader = entry.createReader();
    const childPrefix = prefix ? `${prefix}/${entry.name}` : entry.name;
    // readEntries yields at most ~100 per call and [] when the directory is drained.
    const readBatch = () =>
      new Promise<FsEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    for (let batch = await readBatch(); batch.length; batch = await readBatch()) {
      await Promise.all(batch.map((child) => walkEntry(child, childPrefix, out)));
    }
  }
}
