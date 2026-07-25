/**
 * The files a tool declared for the chat to render.
 *
 * A tool result that means "put these workspace files in front of the user" ends
 * with `[shown-files]{json}` — written by `show_file`, and by the plotting tools
 * via the backend normalising their stdout (`tooling/registry.py`). Everything the
 * card needs is in the declaration: path, mime, size, caption.
 *
 * Replaces `toolImages.ts`, which regex-matched every tool's output text for an
 * `"images": [...]`-shaped array. Hence a fixed marker: prose can't trigger a
 * render, and whether the file exists is settled by the backend, which can look.
 *
 * Mirrors `SHOWN_FILES_MARKER` in `agent/tools.py` — keep them in sync.
 */
const MARKER = "\n[shown-files]";

export type ShownFile = {
  /** Workspace-absolute — hand it straight to `fileUrl` / `openFile`. */
  path: string;
  /** Sniffed by the backend, not derived from the extension. */
  mime: string;
  size: number;
  caption?: string;
};

/** The files `output` declared, or `[]` when it declared none.
 *
 * Never throws. Tool output streams, so a mid-turn call legitimately sees
 * truncated JSON — nothing renders until the declaration is complete. */
export function parseShownFiles(output: string | undefined | null): ShownFile[] {
  if (!output) return [];
  const at = output.lastIndexOf(MARKER);
  if (at < 0) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(output.slice(at + MARKER.length));
  } catch {
    return [];
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
  const raw = (parsed as { shown_files?: unknown }).shown_files;
  if (!Array.isArray(raw)) return [];
  const out: ShownFile[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const { path, mime, size, caption } = entry as Record<string, unknown>;
    // Skip a malformed entry, keep the rest: a card with no path is an <img>
    // with no src.
    if (typeof path !== "string" || !path) continue;
    if (typeof mime !== "string" || !mime) continue;
    if (typeof size !== "number") continue;
    const file: ShownFile = { path, mime, size };
    if (typeof caption === "string" && caption) file.caption = caption;
    out.push(file);
  }
  return out;
}

/** `output` without its declaration — what the tool card body shows.
 *
 * Strips a partial marker too: mid-stream it can arrive before its JSON, and
 * `[shown-fil` in the card is a glitch the user sees. */
export function stripShownFiles(output: string | undefined): string | undefined {
  if (!output) return output;
  const at = output.lastIndexOf(MARKER);
  if (at >= 0) return output.slice(0, at);
  // A trailing prefix of the marker, still arriving.
  for (let n = MARKER.length - 1; n > 0; n--) {
    if (output.endsWith(MARKER.slice(0, n))) return output.slice(0, output.length - n);
  }
  return output;
}

/** Whether the chat shows `file` as the image itself rather than as a card.
 *
 * SVG counts — script inside one does not run when loaded through `<img src>`
 * (only inline `<svg>`, `<iframe>` or `<object>` executes). */
export function isInlineImage(file: ShownFile): boolean {
  return file.mime.startsWith("image/");
}
