/**
 * Shared helpers + constants for the collections grid and the open-collection
 * page — split out of KbCollectionsPage when its grid / page / tabs each got
 * their own URL (#93).
 */

import type { IconName } from "../../components/Icon";

export const ICON_OPTIONS: IconName[] = [
  "layers", "file", "folder", "flame", "bug", "check",
  "settings", "users", "tag", "sparkle", "branch", "git",
  "chat", "filter", "clock", "quote",
];

/** The destination path for one uploaded file. A folder pick carries each
 * file's `webkitRelativePath` (so the tree structure is preserved); fall back
 * to the bare name when it's empty — e.g. a single file chosen in the folder
 * dialog, which otherwise produced an empty path. Mirrors FileTree's rule. */
export function uploadDocPath(
  file: { name: string; webkitRelativePath?: string },
  asFolder: boolean,
): string {
  return (asFolder && file.webkitRelativePath) || file.name;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${Math.round(n / (1024 * 1024))} MB`;
}

export function fmtDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Compact count for the token estimate (≈ bytes/4): 12_400_000 → "12.4 M". */
export function fmtCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)} K`;
  return String(n);
}
