/**
 * The files a tool declared for the chat to render.
 *
 * A tool result that means "put these workspace files in front of the user"
 * carries them under one key, `shown_files` — filled in by `show_file` and by the
 * normalised plotting tools (`tooling/shown_files.py`). Everything the card needs
 * is in the declaration: path, mime, size, caption.
 *
 * Replaces `toolImages.ts`, which regex-matched every tool's output text for an
 * `"images": [...]`-shaped array. Hence one `JSON.parse` of the whole result and
 * one key: prose can't trigger a render, and existence is the backend's check.
 */

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
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
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

/** Whether the chat shows `file` as the image itself rather than as a card.
 *
 * SVG counts — script inside one does not run when loaded through `<img src>`
 * (only inline `<svg>`, `<iframe>` or `<object>` executes). */
export function isInlineImage(file: ShownFile): boolean {
  return file.mime.startsWith("image/");
}
