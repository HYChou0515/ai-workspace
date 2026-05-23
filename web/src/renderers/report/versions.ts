/**
 * Report version derivation. The BE has no ReportVersion resource —
 * versions are a file naming convention. See contract.md §2.6.
 */

import type { FileInfo } from "../../api/types";

export type ReportVersion = {
  v: number;
  path: string;
  isCurrent: boolean;
};

const REPORT_RE = /^\/?report\.v(\d+)\.md$/i;

/**
 * Build the version list from a file listing. Versions are sorted
 * ascending by N. The highest N is `isCurrent: true`.
 */
export function reportVersions(files: FileInfo[]): ReportVersion[] {
  const matches: { v: number; path: string }[] = [];
  for (const f of files) {
    const m = REPORT_RE.exec(f.path);
    if (m && m[1] != null) matches.push({ v: Number.parseInt(m[1], 10), path: f.path });
  }
  if (matches.length === 0) return [];
  matches.sort((a, b) => a.v - b.v);
  const maxV = matches[matches.length - 1]!.v;
  return matches.map((m) => ({ ...m, isCurrent: m.v === maxV }));
}

/** Pick the version to render given an inbound file path. */
export function versionFromPath(
  versions: ReportVersion[],
  path: string,
): ReportVersion | null {
  const m = REPORT_RE.exec(path);
  if (!m || m[1] == null) return null;
  const v = Number.parseInt(m[1], 10);
  return versions.find((r) => r.v === v) ?? null;
}
