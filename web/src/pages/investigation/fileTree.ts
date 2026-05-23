/**
 * Build a nested folder tree from the flat file listing the BE returns.
 * Folders are inferred from path segments (the FileStore has no real
 * directories). Dirs sort before files, both alphabetical.
 */

import type { FileInfo } from "../../api/types";

export type TreeNode = {
  name: string;
  path: string; // "/data" for a folder, "/data/x.csv" for a file
  isDir: boolean;
  size?: number;
  children: TreeNode[];
};

export function buildFileTree(files: FileInfo[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", isDir: true, children: [] };
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((seg, i) => {
      const isLast = i === parts.length - 1;
      const path = "/" + parts.slice(0, i + 1).join("/");
      let child = node.children.find((c) => c.name === seg && c.isDir === !isLast);
      if (!child) {
        child = {
          name: seg,
          path,
          isDir: !isLast,
          size: isLast ? f.size : undefined,
          children: [],
        };
        node.children.push(child);
      }
      node = child;
    });
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
