/**
 * Collapsible file tree with a right-click context menu — the VSCode
 * Explorer for an investigation. Folders are inferred from paths; the
 * mutating actions (new / delete / rename) hit the BE file endpoints and
 * then refresh the listing.
 */

import { useState } from "react";

import { api } from "../../api";
import type { FileInfo } from "../../api/types";
import { Icon } from "../../components/Icon";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { buildFileTree, type TreeNode } from "./fileTree";
import { basename } from "./renderer";

type OpenFn = (path: string, opts?: { preview?: boolean }) => void;

type Menu = { node: TreeNode | null; x: number; y: number };

export function FileTree({
  investigationId,
  files,
  activePath,
  onOpen,
  onOpenInSplit,
  onChanged,
}: {
  investigationId: string;
  files: FileInfo[];
  activePath: string | null;
  onOpen: OpenFn;
  onOpenInSplit?: (path: string) => void;
  onChanged?: () => void;
}) {
  const tree = buildFileTree(files);
  const collapsed = usePersistentSet(`rca:tree-collapsed:${investigationId}`);
  const [menu, setMenu] = useState<Menu | null>(null);

  const refresh = () => onChanged?.();

  const newFile = async (dir: string) => {
    const name = prompt(`New file in ${dir || "/"} — name:`);
    if (!name) return;
    const path = `${dir}/${name}`.replace(/\/+/g, "/");
    await api.writeFile(investigationId, path, "");
    refresh();
    onOpen(path, { preview: false });
  };

  const newFolder = async (dir: string) => {
    const name = prompt(`New folder in ${dir || "/"} — name:`);
    if (!name) return;
    // Folders are implicit; drop a keep-file so the empty folder appears.
    const path = `${dir}/${name}/.keep`.replace(/\/+/g, "/");
    await api.writeFile(investigationId, path, "");
    refresh();
  };

  const rename = async (node: TreeNode) => {
    const next = prompt(`Rename ${node.path} to:`, node.path);
    if (!next || next === node.path) return;
    try {
      await api.moveFile(investigationId, node.path, next);
      refresh();
      if (!node.isDir) onOpen(next, { preview: false });
    } catch (e) {
      alert(`Rename failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const remove = async (node: TreeNode) => {
    if (node.isDir) {
      // delete every file under the folder prefix
      const victims = files.filter((f) => f.path.startsWith(node.path + "/"));
      if (!confirm(`Delete folder ${node.path} and its ${victims.length} file(s)?`)) return;
      for (const v of victims) await api.deleteFile(investigationId, v.path);
    } else {
      if (!confirm(`Delete ${node.path}?`)) return;
      await api.deleteFile(investigationId, node.path);
    }
    refresh();
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", padding: "0 10px 4px" }}>
        <button
          type="button"
          title="New file at root"
          onClick={() => void newFile("")}
          style={{ color: "var(--text-paper-d)" }}
        >
          <Icon name="plus" size={13} />
        </button>
      </div>
      {tree.length === 0 && (
        <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
          No files yet.
        </div>
      )}
      {tree.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          depth={0}
          activePath={activePath}
          collapsed={collapsed}
          onOpen={onOpen}
          onContext={(n, e) => {
            e.preventDefault();
            setMenu({ node: n, x: e.clientX, y: e.clientY });
          }}
        />
      ))}

      {menu?.node && (
        <TreeContextMenu
          node={menu.node}
          x={menu.x}
          y={menu.y}
          canSplit={!!onOpenInSplit && !menu.node.isDir}
          onClose={() => setMenu(null)}
          onNewFile={(dir) => void newFile(dir)}
          onNewFolder={(dir) => void newFolder(dir)}
          onRename={(n) => void rename(n)}
          onDelete={(n) => void remove(n)}
          onCopyPath={(p) => void navigator.clipboard?.writeText(p)}
          onOpenInSplit={onOpenInSplit}
        />
      )}
    </div>
  );
}

function TreeRow({
  node,
  depth,
  activePath,
  collapsed,
  onOpen,
  onContext,
}: {
  node: TreeNode;
  depth: number;
  activePath: string | null;
  collapsed: ReturnType<typeof usePersistentSet>;
  onOpen: OpenFn;
  onContext: (node: TreeNode, e: React.MouseEvent) => void;
}) {
  const indent = 8 + depth * 12;
  const isCollapsed = collapsed.has(node.path);

  if (node.isDir) {
    return (
      <div>
        <button
          type="button"
          onClick={() => collapsed.toggle(node.path)}
          onContextMenu={(e) => onContext(node, e)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            width: "100%",
            padding: `4px 14px 4px ${indent}px`,
            textAlign: "left",
            color: "var(--text-paper-d)",
            fontSize: 12,
          }}
        >
          <Icon name={isCollapsed ? "chev_r" : "chev_d"} size={12} />
          <Icon name="folder" size={13} />
          <span>{node.name}</span>
        </button>
        {!isCollapsed &&
          node.children.map((c) => (
            <TreeRow
              key={c.path}
              node={c}
              depth={depth + 1}
              activePath={activePath}
              collapsed={collapsed}
              onOpen={onOpen}
              onContext={onContext}
            />
          ))}
      </div>
    );
  }

  const active = node.path === activePath;
  return (
    <button
      type="button"
      onClick={() => onOpen(node.path, { preview: true })}
      onDoubleClick={() => onOpen(node.path, { preview: false })}
      onContextMenu={(e) => onContext(node, e)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: "100%",
        padding: `4px 14px 4px ${indent + 16}px`,
        textAlign: "left",
        background: active ? "var(--accent-soft)" : "transparent",
        borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
        color: active ? "var(--accent-h)" : "var(--text-paper)",
        fontSize: 12,
      }}
    >
      <Icon name="file" size={13} color="var(--text-paper-d)" />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {basename(node.path)}
      </span>
    </button>
  );
}

function TreeContextMenu({
  node,
  x,
  y,
  canSplit,
  onClose,
  onNewFile,
  onNewFolder,
  onRename,
  onDelete,
  onCopyPath,
  onOpenInSplit,
}: {
  node: TreeNode;
  x: number;
  y: number;
  canSplit: boolean;
  onClose: () => void;
  onNewFile: (dir: string) => void;
  onNewFolder: (dir: string) => void;
  onRename: (n: TreeNode) => void;
  onDelete: (n: TreeNode) => void;
  onCopyPath: (p: string) => void;
  onOpenInSplit?: (p: string) => void;
}) {
  // For a folder the "containing dir" is itself; for a file it's its parent.
  const dir = node.isDir ? node.path : node.path.split("/").slice(0, -1).join("/");
  const item = (label: string, fn: () => void) => (
    <button
      type="button"
      onClick={() => {
        fn();
        onClose();
      }}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "5px 14px",
        fontSize: 12,
        color: "var(--text-paper)",
        background: "transparent",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-2)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {label}
    </button>
  );
  const sep = <div style={{ height: 1, background: "var(--paper-3)", margin: "4px 0" }} />;
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 80 }} />
      <div
        style={{
          position: "fixed",
          top: y,
          left: x,
          zIndex: 81,
          minWidth: 190,
          background: "var(--white)",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-card)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.16)",
          padding: "4px 0",
        }}
      >
        {!node.isDir && canSplit && onOpenInSplit && item("Open to the side", () => onOpenInSplit(node.path))}
        {item("New file…", () => onNewFile(dir))}
        {item("New folder…", () => onNewFolder(dir))}
        {sep}
        {item("Rename…", () => onRename(node))}
        {item("Delete", () => onDelete(node))}
        {sep}
        {item("Copy path", () => onCopyPath(node.path))}
      </div>
    </>
  );
}
