/**
 * Collapsible file tree with a right-click context menu — the VSCode
 * Explorer for an investigation. Folders are inferred from paths; the
 * mutating actions (new / delete / rename) hit the BE file endpoints and
 * then refresh the listing.
 */

import { useRef, useState } from "react";

import { api } from "../../api";
import type { FileInfo } from "../../api/types";
import { Icon } from "../../components/Icon";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { buildFileTree, type TreeNode } from "./fileTree";
import { basename } from "./renderer";

type OpenFn = (path: string, opts?: { preview?: boolean }) => void;

type Menu = { node: TreeNode | null; x: number; y: number };

const uploadMenuItem: React.CSSProperties = {
  display: "block",
  width: "100%",
  textAlign: "left",
  padding: "5px 14px",
  fontSize: 12,
  color: "var(--text-paper)",
  background: "transparent",
};

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
  // Inline creator (VSCode-style): type the name straight in the tree.
  const [creating, setCreating] = useState<{ kind: "file" | "folder"; dir: string } | null>(null);
  // Inline rename: the path being renamed.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [rootDrop, setRootDrop] = useState(false);
  const [uploadMenu, setUploadMenu] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const refresh = () => onChanged?.();

  const upload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const existing = new Set(files.map((f) => f.path));
    let firstPath: string | null = null;
    for (const f of Array.from(fileList)) {
      if (f.size > 8 * 1024 * 1024) {
        alert(`${f.name} is over the 8 MB cap — skipped.`);
        continue;
      }
      // Preserve folder structure when a directory was picked.
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
      const path = `/${rel}`.replace(/\/+/g, "/");
      if (existing.has(path) && !confirm(`${path} exists. Overwrite?`)) continue;
      await api.writeFile(investigationId, path, f);
      firstPath ??= path;
    }
    refresh();
    if (firstPath) onOpen(firstPath, { preview: false });
  };

  // Move (or Ctrl/⌘-copy) a dragged file into `destDir` ("" = root).
  const dropFileInto = async (srcPath: string, destDir: string, copy: boolean) => {
    const dest = `${destDir}/${basename(srcPath)}`.replace(/\/+/g, "/");
    if (dest === srcPath) return;
    try {
      if (copy) await api.copyFile(investigationId, srcPath, dest);
      else await api.moveFile(investigationId, srcPath, dest);
      refresh();
      if (!copy) onOpen(dest, { preview: false });
    } catch (e) {
      alert(`${copy ? "Copy" : "Move"} failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const readDragFile = (e: React.DragEvent): { path: string } | null => {
    const raw = e.dataTransfer.getData("application/x-rca-file");
    if (!raw) return null;
    try {
      return JSON.parse(raw) as { path: string };
    } catch {
      return null;
    }
  };

  const commitCreate = async (name: string) => {
    const c = creating;
    setCreating(null);
    if (!c || !name.trim()) return;
    const clean = name.trim().replace(/^\/+|\/+$/g, "");
    if (c.kind === "file") {
      const path = `${c.dir}/${clean}`.replace(/\/+/g, "/");
      await api.writeFile(investigationId, path, "");
      refresh();
      onOpen(path, { preview: false });
    } else {
      // Folders are implicit; drop a keep-file so the empty folder shows.
      const path = `${c.dir}/${clean}/.keep`.replace(/\/+/g, "/");
      await api.writeFile(investigationId, path, "");
      if (collapsed.has(`${c.dir}/${clean}`.replace(/\/+/g, "/"))) {
        collapsed.toggle(`${c.dir}/${clean}`.replace(/\/+/g, "/"));
      }
      refresh();
    }
  };

  const commitRename = async (node: TreeNode, name: string) => {
    setRenaming(null);
    const parent = node.path.split("/").slice(0, -1).join("/");
    const next = `${parent}/${name.trim()}`.replace(/\/+/g, "/");
    if (!name.trim() || next === node.path) return;
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
      {/* "Files" header with the three actions: new file, new folder, upload. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "0 10px 4px 14px",
        }}
      >
        <span className="caps" style={{ flex: 1 }}>
          Files
        </span>
        <button
          type="button"
          title="New file"
          onClick={() => setCreating({ kind: "file", dir: "" })}
          style={{ color: "var(--text-paper-d)", padding: 2 }}
        >
          <Icon name="plus" size={13} />
        </button>
        <button
          type="button"
          title="New folder"
          onClick={() => setCreating({ kind: "folder", dir: "" })}
          style={{ color: "var(--text-paper-d)", padding: 2 }}
        >
          <Icon name="folder" size={13} />
        </button>
        <div style={{ position: "relative" }}>
          <button
            type="button"
            title="Upload files or a folder"
            onClick={() => setUploadMenu((v) => !v)}
            style={{ color: "var(--text-paper-d)", padding: 2 }}
          >
            <Icon name="upload" size={13} />
          </button>
          {uploadMenu && (
            <>
              <div
                onClick={() => setUploadMenu(false)}
                style={{ position: "fixed", inset: 0, zIndex: 60 }}
              />
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  right: 0,
                  zIndex: 61,
                  minWidth: 140,
                  background: "var(--white)",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-card)",
                  boxShadow: "0 8px 24px rgba(0,0,0,0.16)",
                  padding: "4px 0",
                }}
              >
                <button
                  type="button"
                  onClick={() => {
                    setUploadMenu(false);
                    fileInputRef.current?.click();
                  }}
                  style={uploadMenuItem}
                >
                  Upload files…
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setUploadMenu(false);
                    folderInputRef.current?.click();
                  }}
                  style={uploadMenuItem}
                >
                  Upload folder…
                </button>
              </div>
            </>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={(e) => {
            void upload(e.target.files);
            e.target.value = "";
          }}
          style={{ display: "none" }}
        />
        <input
          ref={folderInputRef}
          type="file"
          // @ts-expect-error — non-standard but widely supported folder picker
          webkitdirectory=""
          onChange={(e) => {
            void upload(e.target.files);
            e.target.value = "";
          }}
          style={{ display: "none" }}
        />
      </div>

      {/* Tree body — also the root drop zone (move/copy to root). */}
      <div
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("application/x-rca-file")) {
            e.preventDefault();
            setRootDrop(true);
          }
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) setRootDrop(false);
        }}
        onDrop={(e) => {
          setRootDrop(false);
          const d = readDragFile(e);
          if (d) {
            e.preventDefault();
            void dropFileInto(d.path, "", e.ctrlKey || e.metaKey);
          }
        }}
        style={{
          minHeight: 40,
          background: rootDrop ? "var(--accent-soft)" : "transparent",
          borderRadius: 4,
        }}
      >
        {tree.length === 0 && !creating && (
          <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
            No files yet.
          </div>
        )}
        {creating && creating.dir === "" && (
          <InlineEdit
            kind={creating.kind}
            depth={0}
            onCommit={(name) => void commitCreate(name)}
            onCancel={() => setCreating(null)}
          />
        )}
        {tree.map((node) => (
          <TreeRow
            key={node.path}
            node={node}
            depth={0}
            activePath={activePath}
            collapsed={collapsed}
            creating={creating}
            renaming={renaming}
            onOpen={onOpen}
            onCommitCreate={(name) => void commitCreate(name)}
            onCancelCreate={() => setCreating(null)}
            onCommitRename={(n, name) => void commitRename(n, name)}
            onCancelRename={() => setRenaming(null)}
            onDropFile={(srcPath, destDir, copy) => void dropFileInto(srcPath, destDir, copy)}
            readDragFile={readDragFile}
            onContext={(n, e) => {
              e.preventDefault();
              setMenu({ node: n, x: e.clientX, y: e.clientY });
            }}
          />
        ))}
      </div>

      {menu?.node && (
        <TreeContextMenu
          node={menu.node}
          x={menu.x}
          y={menu.y}
          canSplit={!!onOpenInSplit && !menu.node.isDir}
          onClose={() => setMenu(null)}
          onNewFile={(dir) => setCreating({ kind: "file", dir })}
          onNewFolder={(dir) => setCreating({ kind: "folder", dir })}
          onRename={(n) => setRenaming(n.path)}
          onDelete={(n) => void remove(n)}
          onCopyPath={(p) => void navigator.clipboard?.writeText(p)}
          onOpenInSplit={onOpenInSplit}
        />
      )}
    </div>
  );
}

/** Inline name input used for new file/folder + rename. */
function InlineEdit({
  kind,
  depth,
  initial = "",
  onCommit,
  onCancel,
}: {
  kind: "file" | "folder";
  depth: number;
  initial?: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: `2px 14px 2px ${8 + depth * 12}px`,
      }}
    >
      <Icon name={kind === "folder" ? "folder" : "file"} size={13} color="var(--text-paper-d)" />
      <input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onCommit(value);
          else if (e.key === "Escape") onCancel();
        }}
        onBlur={() => (value.trim() ? onCommit(value) : onCancel())}
        placeholder={kind === "folder" ? "folder name" : "file name"}
        style={{
          flex: 1,
          minWidth: 0,
          border: "1px solid var(--accent)",
          borderRadius: 3,
          padding: "1px 4px",
          fontSize: 12,
          outline: "none",
          background: "var(--white)",
          color: "var(--text-paper)",
        }}
      />
    </div>
  );
}

type Creating = { kind: "file" | "folder"; dir: string } | null;

function TreeRow({
  node,
  depth,
  activePath,
  collapsed,
  creating,
  renaming,
  onOpen,
  onContext,
  onCommitCreate,
  onCancelCreate,
  onCommitRename,
  onCancelRename,
  onDropFile,
  readDragFile,
}: {
  node: TreeNode;
  depth: number;
  activePath: string | null;
  collapsed: ReturnType<typeof usePersistentSet>;
  creating: Creating;
  renaming: string | null;
  onOpen: OpenFn;
  onContext: (node: TreeNode, e: React.MouseEvent) => void;
  onCommitCreate: (name: string) => void;
  onCancelCreate: () => void;
  onCommitRename: (node: TreeNode, name: string) => void;
  onCancelRename: () => void;
  onDropFile: (srcPath: string, destDir: string, copy: boolean) => void;
  readDragFile: (e: React.DragEvent) => { path: string } | null;
}) {
  const indent = 8 + depth * 12;
  const isCollapsed = collapsed.has(node.path);
  const [dropOver, setDropOver] = useState(false);

  if (renaming === node.path) {
    return (
      <InlineEdit
        kind={node.isDir ? "folder" : "file"}
        depth={depth + (node.isDir ? 0 : 1)}
        initial={node.name}
        onCommit={(name) => onCommitRename(node, name)}
        onCancel={onCancelRename}
      />
    );
  }

  if (node.isDir) {
    return (
      <div>
        <button
          type="button"
          onClick={() => collapsed.toggle(node.path)}
          onContextMenu={(e) => onContext(node, e)}
          // Drop target: move/copy a dragged file into this folder.
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("application/x-rca-file")) {
              e.preventDefault();
              e.stopPropagation();
              setDropOver(true);
            }
          }}
          onDragLeave={() => setDropOver(false)}
          onDrop={(e) => {
            setDropOver(false);
            const d = readDragFile(e);
            if (d) {
              e.preventDefault();
              e.stopPropagation();
              onDropFile(d.path, node.path, e.ctrlKey || e.metaKey);
            }
          }}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            width: "100%",
            padding: `4px 14px 4px ${indent}px`,
            textAlign: "left",
            color: "var(--text-paper-d)",
            fontSize: 12,
            background: dropOver ? "var(--accent-soft)" : "transparent",
          }}
        >
          <Icon name={isCollapsed ? "chev_r" : "chev_d"} size={12} />
          <Icon name="folder" size={13} />
          <span>{node.name}</span>
        </button>
        {!isCollapsed && (
          <>
            {creating && creating.dir === node.path && (
              <InlineEdit
                kind={creating.kind}
                depth={depth + 1}
                onCommit={onCommitCreate}
                onCancel={onCancelCreate}
              />
            )}
            {node.children.map((c) => (
              <TreeRow
                key={c.path}
                node={c}
                depth={depth + 1}
                activePath={activePath}
                collapsed={collapsed}
                creating={creating}
                renaming={renaming}
                onOpen={onOpen}
                onContext={onContext}
                onCommitCreate={onCommitCreate}
                onCancelCreate={onCancelCreate}
                onCommitRename={onCommitRename}
                onCancelRename={onCancelRename}
                onDropFile={onDropFile}
                readDragFile={readDragFile}
              />
            ))}
          </>
        )}
      </div>
    );
  }

  const active = node.path === activePath;
  return (
    <button
      type="button"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(
          "application/x-rca-file",
          JSON.stringify({ path: node.path }),
        );
        e.dataTransfer.effectAllowed = "copyMove";
      }}
      onClick={() => onOpen(node.path, { preview: true })}
      onDoubleClick={() => onOpen(node.path, { preview: false })}
      onContextMenu={(e) => onContext(node, e)}
      title="Drag onto a folder to move · Ctrl/⌘-drag to copy · drag into a pane to open there"
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
