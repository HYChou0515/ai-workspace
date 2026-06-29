/**
 * Client-side upload pre-block (#325).
 *
 * The backend owns the authoritative upload gate, but for checks it can
 * express as a magic-byte rule it also ships a browser-runnable
 * descriptor via `GET /kb/upload-checks`. We fetch those `UploadCheckHint`s
 * and screen picked files BEFORE uploading, so the common case (an
 * encrypted Office file) is rejected instantly, with no round-trip — and a
 * new browser-checkable rule appears here with zero code change, because
 * this is a generic interpreter of the server's descriptors.
 *
 * A file is blocked when its extension is guarded by a hint AND its
 * leading bytes match one of that hint's forbidden magic prefixes. The
 * server re-runs the same rule, so FE pre-block and BE 422 never disagree;
 * if the hints fetch fails, this screens nothing and the BE 422 stays the
 * gate.
 */

export type UploadCheckHint = {
  id: string;
  /** Lower-case, dot-prefixed extensions this hint guards (".pptx"). */
  extensions: string[];
  /** Hex magic-byte prefixes that mean "unreadable" (lower-case). */
  forbid_magic_hex: string[];
  /** i18n key for the message shown when this hint blocks a file. */
  message_key: string;
};

export type BlockedFile = { file: File; messageKey: string };

export type ScreenResult = { allowed: File[]; blocked: BlockedFile[] };

/** A refused upload as the "can't accept" list holds it: just the name + the
 * i18n key for why (no File handle — it's not getting uploaded). */
export type BlockedUpload = { name: string; messageKey: string };

/** Combine two blocked lists, de-duplicating by file name (the later entry
 * wins). Keeps the "can't accept" list from doubling up when the same file is
 * re-picked or refused twice. */
export function mergeBlocked(existing: BlockedUpload[], added: BlockedUpload[]): BlockedUpload[] {
  const byName = new Map<string, BlockedUpload>();
  for (const b of [...existing, ...added]) byName.set(b.name, b);
  return [...byName.values()];
}

function hintsFor(name: string, hints: UploadCheckHint[]): UploadCheckHint[] {
  const lower = name.toLowerCase();
  return hints.filter((h) => h.extensions.some((ext) => lower.endsWith(ext)));
}

function toHex(bytes: Uint8Array): string {
  let out = "";
  for (const b of bytes) out += b.toString(16).padStart(2, "0");
  return out;
}

/** The message key if `file` is blocked by any applicable hint, else null. */
export async function screenFile(file: File, hints: UploadCheckHint[]): Promise<string | null> {
  const applicable = hintsFor(file.name, hints);
  if (applicable.length === 0) return null;
  const maxBytes = Math.max(
    ...applicable.flatMap((h) => h.forbid_magic_hex.map((hex) => hex.length / 2)),
  );
  const head = toHex(new Uint8Array(await file.slice(0, maxBytes).arrayBuffer()));
  for (const h of applicable) {
    for (const magic of h.forbid_magic_hex) {
      if (head.startsWith(magic.toLowerCase())) return h.message_key;
    }
  }
  return null;
}

/** Partition `files` into those safe to upload and those the hints block. */
export async function screenFiles(files: File[], hints: UploadCheckHint[]): Promise<ScreenResult> {
  const allowed: File[] = [];
  const blocked: BlockedFile[] = [];
  for (const file of files) {
    const messageKey = await screenFile(file, hints);
    if (messageKey) blocked.push({ file, messageKey });
    else allowed.push(file);
  }
  return { allowed, blocked };
}
