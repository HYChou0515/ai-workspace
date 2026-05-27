/**
 * Pure path utilities for the workspace file tree / breadcrumbs.
 *
 * The file-type → renderer dispatch (pickRenderer / isRawEditorView /
 * hasOutline / the renderer table) lives in `renderers/registry.ts` — the one
 * place to add a preview type. `imageMime` stays here (a pure helper the image
 * renderer uses; keeping it out of the registry avoids an import cycle).
 */

/** MIME type for an image path, for building a Blob URL from edited bytes. */
export function imageMime(path: string): string {
  const ext = path.toLowerCase().split(".").pop() ?? "";
  switch (ext) {
    case "png":
      return "image/png";
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "gif":
      return "image/gif";
    case "svg":
      return "image/svg+xml";
    case "webp":
      return "image/webp";
    case "bmp":
      return "image/bmp";
    default:
      return "application/octet-stream";
  }
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
