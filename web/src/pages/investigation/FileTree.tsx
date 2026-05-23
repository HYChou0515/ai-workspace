/**
 * Collapsible file tree with a right-click context menu — the VSCode
 * Explorer for an investigation. Folders are inferred from paths; the
 * mutating actions (new / delete / rename) hit the BE file endpoints and
 * then refresh the listing.
 */

import { useRef, useState } from "react";

import { api } from "../../api";
import type { FileInfo } from "../../api/types";
import { useDialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { buildFileTree, type TreeNode } from "./fileTree";
import { basename } from "./renderer";
import { nextSelection, type SelState, topLevel, visibleOrder } from "./treeSelection";

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
  dirs = [],
  activePath,
  onOpen,
  onOpenInSplit,
  onChanged,
}: {
  investigationId: string;
  files: FileInfo[];
  dirs?: string[];
  activePath: string | null;
  onOpen: OpenFn;
  onOpenInSplit?: (path: string) => void;
  onChanged?: () => void;
}) {
  const dialog = useDialog();
  const tree = buildFileTree(files, dirs);
  const collapsed = usePersistentSet(`rca:tree-collapsed:${investigationId}`);
  const [menu, setMenu] = useState<Menu | null>(null);
  // Inline creator (VSCode-style): type the name straight in the tree.
  const [creating, setCreating] = useState<{ kind: "file" | "folder"; dir: string } | null>(null);
  // Inline rename: the path being renamed.
  const [renaming, setRenaming] = useState<string | null>(null);
  // Multi-selection (VSCode): ctrl/shift/ctrl+shift click. `anchor` is the
  // last-clicked node — new file/folder is born relative to it.
  const [sel, setSel] = useState<SelState>({ selected: [], anchor: null });
  const selectedSet = new Set(sel.selected);
  const order = visibleOrder(tree, (p) => collapsed.has(p));
  const [rootDrop, setRootDrop] = useState(false);
  const [uploadMenu, setUploadMenu] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Where a new file/folder lands: inside the anchored folder, or beside
  // the anchored file, else the root.
  const createDir = (() => {
    const anchor = sel.anchor;
    if (!anchor) return "";
    const isFolder = dirs.includes(anchor) || files.some((f) => f.path.startsWith(anchor + "/"));
    return isFolder ? anchor : anchor.split("/").slice(0, -1).join("/");
  })();
  const folderInputRef = useRef<HTMLInputElement>(null);

  // Click on a row: update the selection; a plain (unmodified) click also
  // opens a file / toggles a folder. Modifier clicks only adjust selection.
  const activate = (node: TreeNode, e: React.MouseEvent) => {
    const mods = { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey };
    setSel((s) => nextSelection(s, node.path, mods, order));
    if (mods.ctrl || mods.shift) return;
    if (node.isDir) collapsed.toggle(node.path);
    else onOpen(node.path, { preview: true });
  };

  // Paths to act on for a node-targeted action: the whole selection when the
  // node is part of a multi-selection, else just the node.
  const targetsFor = (path: string): string[] =>
    selectedSet.has(path) && sel.selected.length > 1 ? sel.selected : [path];

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

  // Move (or Ctrl/⌘-copy) dragged files/folders into `destDir` ("" = root).
  // The BE handles folders atomically (subtree move/copy).
  // Returns true if it's safe to write/move onto `dest`: either nothing was
  // there, or the user confirmed Replace (in which case the target is
  // deleted first). VSCode-style replace prompt, shared by move/copy,
  // rename and new file/folder so the BE never has to clobber.
  const ensureReplaceable = async (dest: string): Promise<boolean> => {
    const exists = files.some((f) => f.path === dest) || dirs.includes(dest);
    if (!exists) return true;
    const choice = await dialog.confirm({
      title: "Replace existing item",
      body: `“${basename(dest)}” already exists. Replace it?`,
      actions: [
        { id: "replace", label: "Replace", variant: "danger" },
        { id: "cancel", label: "Cancel" },
      ],
    });
    if (choice !== "replace") return false;
    await api.deleteFile(investigationId, dest);
    return true;
  };

  const dropFileInto = async (srcPaths: string[], destDir: string, copy: boolean) => {
    // Moving a folder already relocates its subtree; drop any selected
    // descendants so we don't then act on a path that no longer exists.
    const tops = topLevel(srcPaths);
    try {
      for (const srcPath of tops) {
        const destBase = `${destDir}/${basename(srcPath)}`.replace(/\/+/g, "/");
        if (destBase === srcPath || destBase.startsWith(srcPath + "/")) continue; // into-self
        if (!(await ensureReplaceable(destBase))) continue;
        if (copy) await api.copyFile(investigationId, srcPath, destBase);
        else await api.moveFile(investigationId, srcPath, destBase);
      }
      refresh();
      if (!copy && tops.length === 1) {
        const only = tops[0]!;
        const isFolder = dirs.includes(only) || files.some((f) => f.path.startsWith(only + "/"));
        if (!isFolder) onOpen(`${destDir}/${basename(only)}`.replace(/\/+/g, "/"), { preview: false });
      }
    } catch (e) {
      alert(`${copy ? "Copy" : "Move"} failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const readDragFile = (e: React.DragEvent): { paths: string[] } | null => {
    const raw = e.dataTransfer.getData("application/x-rca-file");
    if (!raw) return null;
    try {
      const d = JSON.parse(raw) as { path?: string; paths?: string[] };
      const paths = d.paths ?? (d.path ? [d.path] : []);
      return paths.length ? { paths } : null;
    } catch {
      return null;
    }
  };

  const commitCreate = async (name: string) => {
    const c = creating;
    setCreating(null);
    if (!c || !name.trim()) return;
    const clean = name.trim().replace(/^\/+|\/+$/g, "");
    const path = `${c.dir}/${clean}`.replace(/\/+/g, "/");
    try {
      if (!(await ensureReplaceable(path))) return;
      if (c.kind === "file") {
        await api.writeFile(investigationId, path, "");
        refresh();
        onOpen(path, { preview: false });
      } else {
        // Real, honest folder — no .keep placeholder.
        await api.mkdir(investigationId, path);
        if (collapsed.has(path)) collapsed.toggle(path);
        refresh();
      }
    } catch (e) {
      alert(`Create failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const commitRename = async (node: TreeNode, name: string) => {
    setRenaming(null);
    const parent = node.path.split("/").slice(0, -1).join("/");
    const next = `${parent}/${name.trim()}`.replace(/\/+/g, "/");
    if (!name.trim() || next === node.path) return;
    try {
      if (!(await ensureReplaceable(next))) return;
      await api.moveFile(investigationId, node.path, next);
      refresh();
      if (!node.isDir) onOpen(next, { preview: false });
    } catch (e) {
      alert(`Rename failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // Delete one or many paths (the BE removes a folder's whole subtree in a
  // single call). Confirms through the modal dialog.
  const deletePaths = async (paths: string[]) => {
    // Deleting a folder removes its subtree, so prune selected descendants.
    const tops = topLevel(paths);
    if (tops.length === 0) return;
    const body =
      tops.length === 1
        ? `Delete ${tops[0]}? This cannot be undone.`
        : `Delete these ${tops.length} items? This cannot be undone.`;
    const choice = await dialog.confirm({
      title: tops.length === 1 ? "Delete item" : `Delete ${tops.length} items`,
      body,
      actions: [
        { id: "delete", label: "Delete", variant: "danger" },
        { id: "cancel", label: "Cancel" },
      ],
    });
    if (choice !== "delete") return;
    for (const p of tops) await api.deleteFile(investigationId, p);
    setSel({ selected: [], anchor: null });
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
          title={createDir ? `New file in ${createDir}/` : "New file"}
          onClick={() => {
            if (createDir && collapsed.has(createDir)) collapsed.toggle(createDir);
            setCreating({ kind: "file", dir: createDir });
          }}
          style={{ color: "var(--text-paper-d)", padding: 2 }}
        >
          <Icon name="plus" size={13} />
        </button>
        <button
          type="button"
          title={createDir ? `New folder in ${createDir}/` : "New folder"}
          onClick={() => {
            if (createDir && collapsed.has(createDir)) collapsed.toggle(createDir);
            setCreating({ kind: "folder", dir: createDir });
          }}
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

      {/* Tree body — also the root drop zone (move/copy to root). Only the
          genuine empty area highlights, so dragging a file no longer tints
          the whole tree: we ignore dragover that bubbled up from a row. */}
      <div
        onDragOver={(e) => {
          if (
            e.target === e.currentTarget &&
            e.dataTransfer.types.includes("application/x-rca-file")
          ) {
            e.preventDefault();
            setRootDrop(true);
          }
        }}
        onDragLeave={(e) => {
          if (e.target === e.currentTarget) setRootDrop(false);
        }}
        onDrop={(e) => {
          setRootDrop(false);
          if (e.target !== e.currentTarget) return; // a row handled it
          const d = readDragFile(e);
          if (d) {
            e.preventDefault();
            void dropFileInto(d.paths, "", e.ctrlKey || e.metaKey);
          }
        }}
        tabIndex={0}
        onKeyDown={(e) => {
          if (sel.selected.length === 0) return;
          if (e.key === "Delete" || e.key === "Backspace") {
            e.preventDefault();
            void deletePaths(sel.selected);
          } else if (e.key === "Enter") {
            // open every selected file (folders ignored)
            e.preventDefault();
            for (const p of sel.selected) {
              if (!dirs.includes(p) && !files.some((f) => f.path.startsWith(p + "/"))) {
                onOpen(p, { preview: false });
              }
            }
          }
        }}
        style={{
          minHeight: 40,
          // dashed outline on the empty area only — never a full-tree fill
          outline: rootDrop ? "1px dashed var(--accent)" : "none",
          outlineOffset: -2,
          borderRadius: 4,
          paddingBottom: 24,
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
            selectedSet={selectedSet}
            multi={sel.selected.length > 1}
            collapsed={collapsed}
            creating={creating}
            renaming={renaming}
            onOpen={onOpen}
            onActivate={activate}
            onDoubleOpen={(p) => {
              for (const t of targetsFor(p)) {
                if (!dirs.includes(t) && !files.some((f) => f.path.startsWith(t + "/"))) {
                  onOpen(t, { preview: false });
                }
              }
            }}
            dragPathsFor={targetsFor}
            onCommitCreate={(name) => void commitCreate(name)}
            onCancelCreate={() => setCreating(null)}
            onCommitRename={(n, name) => void commitRename(n, name)}
            onCancelRename={() => setRenaming(null)}
            onDropFile={(srcPaths, destDir, copy) => void dropFileInto(srcPaths, destDir, copy)}
            readDragFile={readDragFile}
            onContext={(n, e) => {
              e.preventDefault();
              // right-clicking outside the current selection re-selects just it
              if (!selectedSet.has(n.path)) setSel({ selected: [n.path], anchor: n.path });
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
          onDelete={(n) => void deletePaths(targetsFor(n.path))}
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
  selectedSet,
  multi,
  collapsed,
  creating,
  renaming,
  onOpen,
  onActivate,
  onDoubleOpen,
  dragPathsFor,
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
  selectedSet: Set<string>;
  multi: boolean;
  collapsed: ReturnType<typeof usePersistentSet>;
  creating: Creating;
  renaming: string | null;
  onOpen: OpenFn;
  onActivate: (node: TreeNode, e: React.MouseEvent) => void;
  onDoubleOpen: (path: string) => void;
  dragPathsFor: (path: string) => string[];
  onContext: (node: TreeNode, e: React.MouseEvent) => void;
  onCommitCreate: (name: string) => void;
  onCancelCreate: () => void;
  onCommitRename: (node: TreeNode, name: string) => void;
  onCancelRename: () => void;
  onDropFile: (srcPaths: string[], destDir: string, copy: boolean) => void;
  readDragFile: (e: React.DragEvent) => { paths: string[] } | null;
}) {
  const indent = 8 + depth * 12;
  const isCollapsed = collapsed.has(node.path);
  const [dropOver, setDropOver] = useState(false);
  const [dragging, setDragging] = useState(false);

  if (renaming === node.path) {
    return (
      <InlineEdit
        kind={node.isDir ? "folder" : "file"}
        depth={depth}
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
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData(
              "application/x-rca-file",
              JSON.stringify({ path: node.path, paths: dragPathsFor(node.path) }),
            );
            e.dataTransfer.effectAllowed = "copyMove";
            setDragging(true);
          }}
          onDragEnd={() => setDragging(false)}
          onClick={(e) => onActivate(node, e)}
          onContextMenu={(e) => onContext(node, e)}
          // Drop target: move/copy a dragged file or folder into this folder.
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
              onDropFile(d.paths, node.path, e.ctrlKey || e.metaKey);
            }
          }}
          title="Drag onto another folder to move · Ctrl/⌘ to copy"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            width: "100%",
            padding: `4px 14px 4px ${indent}px`,
            textAlign: "left",
            color: "var(--text-paper-d)",
            fontSize: 12,
            background: dropOver
              ? "var(--accent-soft)"
              : selectedSet.has(node.path)
                ? "var(--paper-2)"
                : "transparent",
            borderLeft: selectedSet.has(node.path)
              ? "2px solid var(--accent)"
              : "2px solid transparent",
            opacity: dragging ? 0.4 : 1,
          }}
        >
          {/* A folder is just a chevron twistie, sized like a file icon, so
              folders and files at the same depth line up (VSCode). */}
          <Icon name={isCollapsed ? "chev_r" : "chev_d"} size={13} />
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
                selectedSet={selectedSet}
                multi={multi}
                collapsed={collapsed}
                creating={creating}
                renaming={renaming}
                onOpen={onOpen}
                onActivate={onActivate}
                onDoubleOpen={onDoubleOpen}
                dragPathsFor={dragPathsFor}
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
  const selected = selectedSet.has(node.path);
  return (
    <button
      type="button"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(
          "application/x-rca-file",
          JSON.stringify({ path: node.path, paths: dragPathsFor(node.path) }),
        );
        e.dataTransfer.effectAllowed = "copyMove";
        setDragging(true);
      }}
      onDragEnd={() => setDragging(false)}
      onClick={(e) => onActivate(node, e)}
      onDoubleClick={() => onDoubleOpen(node.path)}
      onContextMenu={(e) => onContext(node, e)}
      title={
        multi && selected
          ? "Drag to move all selected · Ctrl/⌘ to copy"
          : "Drag onto a folder to move · Ctrl/⌘-drag to copy · drag into a pane to open there"
      }
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: "100%",
        padding: `4px 14px 4px ${indent}px`,
        textAlign: "left",
        background: active ? "var(--accent-soft)" : selected ? "var(--paper-2)" : "transparent",
        borderLeft:
          active || selected ? "2px solid var(--accent)" : "2px solid transparent",
        color: active ? "var(--accent-h)" : "var(--text-paper)",
        fontSize: 12,
        opacity: dragging ? 0.4 : 1,
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
