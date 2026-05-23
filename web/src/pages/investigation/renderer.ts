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

/** Does this file type have markdown headings worth showing in the
 * Outline panel? Both the markdown renderer and the report renderer
 * (which wraps /report.v*.md) render markdown bodies. */
export function hasOutline(path: string): boolean {
  const kind = pickRenderer(path);
  return kind === "markdown" || kind === "report";
}

/** Basename of a file path. Used in tab strip and breadcrumb. */
export function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  return i === -1 ? trimmed : trimmed.slice(i + 1);
}

/** Directory portion of a path, leading slash stripped, for the search
 * panel's dim file-location label. Root-level files return "". */
export function dirname(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  if (i <= 0) return "";
  return trimmed.slice(0, i).replace(/^\/+/, "");
}

export type DirEntry = { name: string; path: string; isDir: boolean };

/** Immediate children of directory `dir` (no leading slash, "" = root)
 * derived from a flat path list. Folders first (alpha), then files
 * (alpha). Folder `path` is the dir key (re-feedable to dirChildren);
 * file `path` is the full "/..." path (feedable to onOpenFile). */
export function dirChildren(paths: string[], dir: string): DirEntry[] {
  const prefix = dir ? `${dir}/` : "";
  const dirs = new Set<string>();
  const filesOut: DirEntry[] = [];
  for (const raw of paths) {
    const rel = raw.replace(/^\/+/, "");
    if (!rel.startsWith(prefix)) continue;
    const rest = rel.slice(prefix.length);
    if (!rest) continue;
    const slash = rest.indexOf("/");
    if (slash === -1) {
      filesOut.push({ name: rest, path: `/${rel}`, isDir: false });
    } else {
      dirs.add(rest.slice(0, slash));
    }
  }
  const dirsOut: DirEntry[] = [...dirs]
    .sort((a, b) => a.localeCompare(b))
    .map((name) => ({ name, path: `${prefix}${name}`, isDir: true }));
  filesOut.sort((a, b) => a.name.localeCompare(b.name));
  return [...dirsOut, ...filesOut];
}

/** Path segments excluding root and basename, for breadcrumb display. */
export function breadcrumbSegments(path: string): string[] {
  const parts = path.split("/").filter((p) => p.length > 0);
  if (parts.length <= 1) return [];
  return parts.slice(0, -1);
}
