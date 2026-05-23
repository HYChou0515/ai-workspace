/**
 * Build a nested folder tree from the flat file listing the BE returns,
 * plus an explicit `dirs` list so empty folders (which have no files to
 * infer them from) still appear. Dirs sort before files, both alphabetical.
 */

import type { FileInfo } from "../../api/types";

export type TreeNode = {
  name: string;
  path: string; // "/data" for a folder, "/data/x.csv" for a file
  isDir: boolean;
  size?: number;
  children: TreeNode[];
};

export function buildFileTree(files: FileInfo[], dirs: string[] = []): TreeNode[] {
  const root: TreeNode = { name: "", path: "", isDir: true, children: [] };

  // Walk to (creating) the folder node at `path`.
  const ensureDir = (path: string): TreeNode => {
    const parts = path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((seg, i) => {
      const segPath = "/" + parts.slice(0, i + 1).join("/");
      let child = node.children.find((c) => c.name === seg && c.isDir);
      if (!child) {
        child = { name: seg, path: segPath, isDir: true, children: [] };
        node.children.push(child);
      }
      node = child;
    });
    return node;
  };

  for (const dir of dirs) ensureDir(dir);

  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    const fileName = parts[parts.length - 1]!;
    const parent = parts.length > 1 ? ensureDir("/" + parts.slice(0, -1).join("/")) : root;
    if (!parent.children.some((c) => c.name === fileName && !c.isDir)) {
      parent.children.push({
        name: fileName,
        path: f.path,
        isDir: false,
        size: f.size,
        children: [],
      });
    }
  }

  sortTree(root);
  return root.children;
}

function sortTree(node: TreeNode): void {
  node.children.sort((a, b) =>
    a.isDir !== b.isDir ? (a.isDir ? -1 : 1) : a.name.localeCompare(b.name),
  );
  node.children.forEach(sortTree);
}
