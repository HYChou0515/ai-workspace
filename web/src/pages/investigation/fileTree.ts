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
  /** A folder the listing did NOT enter (pruned as derived, or past the entry
   * budget): it is on the tree, collapsed, and its contents are fetched when
   * it is expanded. An entered-but-empty folder is not lazy — there is
   * nothing to fetch for it. Only folders carry this. */
  lazy: boolean;
};

export function buildFileTree(
  files: FileInfo[],
  dirs: string[] = [],
  unwalked: string[] = [],
): TreeNode[] {
  const root: TreeNode = { name: "", path: "", isDir: true, children: [], lazy: false };
  const lazyPaths = new Set(unwalked);

  // Both inserts below used to scan the parent's existing children, which is
  // O(N²) in the files sharing one directory — and the constant is the name
  // length, since real doc names share long prefixes and compare slowly. A KB
  // collection uploaded flat hits exactly that: 8000 files took 1.08 s to
  // build, and the tree is rebuilt on every render (every keystroke in the
  // filter box), so the page stalls. These two indexes answer the same two
  // questions the scans did, in O(1).
  const dirByPath = new Map<string, TreeNode>(); // segPath  -> dir node
  const fileKeys = new Set<string>(); // parentPath \0 name

  // Walk to (creating) the folder node at `path`.
  const ensureDir = (path: string): TreeNode => {
    const parts = path.split("/").filter(Boolean);
    let node = root;
    let segPath = "";
    for (const seg of parts) {
      segPath += "/" + seg;
      let child = dirByPath.get(segPath);
      if (!child) {
        child = {
          name: seg,
          path: segPath,
          isDir: true,
          children: [],
          lazy: lazyPaths.has(segPath),
        };
        node.children.push(child);
        dirByPath.set(segPath, child);
      }
      node = child;
    }
    return node;
  };

  for (const dir of dirs) ensureDir(dir);

  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    const fileName = parts[parts.length - 1]!;
    // Key on the NORMALISED parent + name, which is what the old scan compared
    // — not on f.path, whose raw form ("/a//b.csv") may differ while resolving
    // to the same node. The first path to arrive is still the one kept.
    const parentPath = parts.length > 1 ? "/" + parts.slice(0, -1).join("/") : "";
    // NUL separates: the one character a path segment cannot contain, so
    // ("/a b", "c") and ("/a", "b c") cannot collide into one key.
    const key = parentPath + "\u0000" + fileName;
    if (fileKeys.has(key)) continue;
    fileKeys.add(key);
    const parent = parts.length > 1 ? ensureDir(parentPath) : root;
    parent.children.push({
      name: fileName,
      path: f.path,
      isDir: false,
      size: f.size,
      children: [],
      lazy: false,
    });
  }

  sortTree(root);
  return root.children;
}

export type Presence = "present" | "absent" | "unknown";

/**
 * Whether `path` exists, as far as ONE listing can tell. The listing is a
 * pruned preload: folders in `unwalked` were listed but never entered, so a
 * file under one of them is simply not in `files` — not gone. "Not in the
 * list" therefore splits into two answers, and every rule that used to close
 * a tab, drop a recent file or forget an initial tab on "not in the list"
 * must act only on `absent`.
 *
 * Deliberately conservative: anything under an unwalked folder is `unknown`
 * even once that folder's level has been fetched. Deciding `absent` from a
 * loaded level would make "does this file exist" change with which folders
 * happen to be open — a presence that follows UI state is the kind of wrong
 * a review does not catch. The cost is a deleted file under `node_modules/`
 * keeping its tab until the user closes it.
 */
export function presenceOf(
  path: string,
  files: ReadonlySet<string>,
  unwalked: readonly string[],
): Presence {
  if (files.has(path)) return "present";
  for (const dir of unwalked) if (path.startsWith(dir + "/")) return "unknown";
  return "absent";
}

/**
 * Filter a built tree down to the nodes whose (case-insensitive) full path
 * contains `term`. A directory survives if it matches directly OR has any
 * surviving descendant; every surviving directory is returned in `expand` so
 * the caller can force those ancestors open and reveal the matches. An empty
 * / whitespace-only `term` is a no-op: the original tree is returned unchanged
 * with an empty `expand` set (so the caller keeps the user's collapse state).
 */
export function pruneTree(
  tree: TreeNode[],
  term: string,
): { tree: TreeNode[]; expand: Set<string> } {
  const needle = term.trim().toLowerCase();
  if (!needle) return { tree, expand: new Set() };

  const expand = new Set<string>();
  const filter = (nodes: TreeNode[]): TreeNode[] => {
    const out: TreeNode[] = [];
    for (const n of nodes) {
      const selfMatch = n.path.toLowerCase().includes(needle);
      if (!n.isDir) {
        if (selfMatch) out.push(n);
        continue;
      }
      const kids = filter(n.children);
      if (selfMatch || kids.length > 0) {
        out.push({ ...n, children: kids });
        expand.add(n.path);
      }
    }
    return out;
  };

  return { tree: filter(tree), expand };
}

// `a.localeCompare(b)` builds a fresh collator on every call; one reused
// collator is the same ordering (identical defaults) for a fraction of the cost,
// and sorting is the other half of the build with real, long, prefix-sharing
// names. See https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/
// Global_Objects/Intl/Collator — "when comparing large numbers of strings".
const byName = new Intl.Collator(undefined, { usage: "sort" });

function sortTree(node: TreeNode): void {
  node.children.sort((a, b) =>
    a.isDir !== b.isDir ? (a.isDir ? -1 : 1) : byName.compare(a.name, b.name),
  );
  node.children.forEach(sortTree);
}
