/**
 * File-type → renderer mapping. The "views" in the design (brief / SPC /
 * Pareto / fishbone / 5-Why / report) are just renderers picked by
 * extension + filename. See contract.md §5.
 */

export type RendererKey =
  | "markdown"
  | "notebook"
  | "fishbone"
  | "report"
  | "csv"
  | "json"
  | "image"
  | "text";

export function pickRenderer(path: string): RendererKey {
  // Report version files take a dedicated renderer that knows about /report.v*.md.
  if (/^\/?report\.v\d+\.md$/.test(path)) return "report";

  const ext = path.toLowerCase().split(".").pop() ?? "";
  switch (ext) {
    case "md":
    case "markdown":
      return "markdown";
    case "ipynb":
      return "notebook";
    case "canvas":
      return "fishbone";
    case "csv":
    case "tsv":
      return "csv";
    case "json":
      return "json";
    case "png":
    case "jpg":
    case "jpeg":
    case "gif":
    case "svg":
    case "webp":
      return "image";
    default:
      return "text";
  }
}

/** Basename of a file path. Used in tab strip and breadcrumb. */
export function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  return i === -1 ? trimmed : trimmed.slice(i + 1);
}

/** Path segments excluding root and basename, for breadcrumb display. */
export function breadcrumbSegments(path: string): string[] {
  const parts = path.split("/").filter((p) => p.length > 0);
  if (parts.length <= 1) return [];
  return parts.slice(0, -1);
}
