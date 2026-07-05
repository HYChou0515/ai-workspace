/**
 * Collapsible file tree with a right-click context menu — the VSCode
 * Explorer for an investigation. Folders are inferred from paths; the
 * mutating actions (new / delete / rename) hit the BE file endpoints and
 * then refresh the listing.
 */

import { useRef, useState } from "react";

import { type FileCaps, type FileService, useOptionalFileService } from "../../api/fileService";
import type { FileInfo } from "../../api/types";
import { useOptionalDialog } from "../../components/Dialog";
import { Icon } from "../../components/Icon";
import { usePersistentSet } from "../../hooks/usePersistentSet";
import { buildFileTree, pruneTree, type TreeNode } from "./fileTree";
import { basename } from "./renderer";
import { nextSelection, type SelState, topLevel, visibleOrder } from "./treeSelection";
import { folderState, toggleSubtree } from "./treeCheckbox";
import { extractClipboardFiles, readTransferEntries } from "./transfer";
import { pxToRem } from "../../lib/pxToRem";

// #364: a drag carrying OS files/folders (not one of our internal reorder payloads).
const isExternalDrag = (e: React.DragEvent): boolean =>
  !e.dataTransfer.types.includes("application/x-rca-file") &&
  e.dataTransfer.types.includes("Files");

type OpenFn = (path: string, opts?: { preview?: boolean }) => void;

type Menu = { node: TreeNode | null; x: number; y: number };

/** Opt-in controlled multi-select mode (#415 card-gen picker): the tree becomes
 * a read-only checkbox picker over its leaf files. `selected` is the set of
 * checked leaf paths (folders are a derived tri-state); toggling any row (leaf
 * or a whole folder subtree) calls `onChange` with the next set. */
export type SelectMode = {
  selected: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
};

// Stable empty set for the no-filter case, so we don't re-alloc every render.
const NO_FORCE_OPEN: ReadonlySet<string> = new Set();

// A service-less tree (select mode) can't mutate — every action is hidden.
const NO_CAPS: FileCaps = {
  write: false,
  create: false,
  upload: false,
  delete: false,
  move: false,
  copy: false,
  folders: false,
  download: false,
};

// Stand-in service for a tree rendered without a <FileServiceProvider> (select
// mode). Its NO_CAPS hide every mutation control, so these methods are never
// reached — the shell just needs a non-null service to read caps/scopeId from.
const NO_SERVICE: FileService = {
  scopeId: "",
  caps: NO_CAPS,
  listFiles: async () => [],
  listDirs: async () => [],
  readFile: async () => {
    throw new Error("no file service");
  },
  writeFile: async () => {},
  deleteFile: async () => {},
  moveFile: async () => {},
  copyFile: async () => {},
  mkdir: async () => {},
  refreshFiles: async () => {},
  fileUrl: () => "",
  fileDownloadUrl: () => "",
  prepareDirDownload: async () => {
    throw new Error("no file service");
  },
  dirDownloadUrl: () => "",
};

const uploadMenuItem: React.CSSProperties = {
  display: "block",
  width: "100%",
  textAlign: "left",
  padding: "5px 14px",
  fontSize: pxToRem(12),
  color: "var(--text-paper)",
  background: "transparent",
};

export function FileTree({
  files,
  dirs = [],
  activePath,
  onOpen,
  onOpenInSplit,
  onChanged,
  onReindex,
  decorate,
  searchable = false,
  select,
  scopeId,
}: {
  files: FileInfo[];
  dirs?: string[];
  activePath: string | null;
  onOpen: OpenFn;
  onOpenInSplit?: (path: string) => void;
  onChanged?: () => void;
  /** Re-index the given paths (KB doc IDE). Omitted → no "Reindex" menu item
   * (the investigation workspace + wiki have nothing to re-index). */
  onReindex?: (paths: string[]) => void;
  /** Optional trailing badge per file row (e.g. KB indexing / unsaved dot).
   * Omitted → no badges (the investigation workspace looks unchanged). */
  decorate?: (path: string) => React.ReactNode;
  /** #402: show a client-side name/path filter in the header. Opt-in — the KB
   * doc + wiki IDEs pass it; the investigation workspace (which has its own
   * content SearchPanel) leaves it off and looks unchanged. */
  searchable?: boolean;
  /** #415: opt-in controlled multi-select — turns the tree into a read-only
   * checkbox picker over its leaf files. Omitted → the normal open/edit tree. */
  select?: SelectMode;
  /** Collapse-state persistence scope when there's no FileService in context
   * (select mode feeds its own list). Ignored when a service is present. */
  scopeId?: string;
}) {
  // Select mode (the card-gen picker) runs WITHOUT a writable service — it feeds
  // its own file list and never mutates — so the service is optional and its
  // capabilities collapse to none (every mutation action hides).
  const svc = useOptionalFileService() ?? NO_SERVICE;
  const caps = svc.caps;
  // Select mode has no confirm prompts (they're on caps-gated mutations), so a
  // no-op stands in when there's no <DialogProvider>.
  const dialog = useOptionalDialog() ?? { confirm: async () => null };
  const [query, setQuery] = useState("");
  const fullTree = buildFileTree(files, dirs);
  // While a filter is active, `pruneTree` returns only the matching branches
  // plus the ancestor dirs to force open; an empty term is a no-op (full tree,
  // nothing forced) so the user's own collapse state is preserved (#402).
  const { tree, expand } = searchable
    ? pruneTree(fullTree, query)
    : { tree: fullTree, expand: NO_FORCE_OPEN };
  const collapsed = usePersistentSet(`rca:tree-collapsed:${svc.scopeId || scopeId || "default"}`);
  const [menu, setMenu] = useState<Menu | null>(null);
  // Inline creator (VSCode-style): type the name straight in the tree.
  const [creating, setCreating] = useState<{ kind: "file" | "folder"; dir: string } | null>(null);
  // Inline rename: the path being renamed.
  const [renaming, setRenaming] = useState<string | null>(null);
  // Multi-selection (VSCode): ctrl/shift/ctrl+shift click. `anchor` is the
  // last-clicked node — new file/folder is born relative to it.
  const [sel, setSel] = useState<SelState>({ selected: [], anchor: null });
  const selectedSet = new Set(sel.selected);
  // A force-open (filter) ancestor counts as expanded for navigation order too.
  const order = visibleOrder(tree, (p) => collapsed.has(p) && !expand.has(p));
  const [rootDrop, setRootDrop] = useState(false);
  const [uploadMenu, setUploadMenu] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Set by the folder context menu just before it opens the file picker, so the
  // resulting upload targets that folder regardless of the current selection;
  // null → the toolbar button, which falls back to the anchored `createDir`.
  const uploadDirRef = useRef<string | null>(null);

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

  // Upload into `targetDir` ("" = root). Defaults to the anchored folder so the
  // toolbar button drops files where the rest of the create actions land; the
  // folder context menu passes an explicit dir.
  const upload = async (fileList: FileList | File[] | null, targetDir: string = createDir) => {
    if (!fileList || fileList.length === 0) return;
    const existing = new Set(files.map((f) => f.path));
    let firstPath: string | null = null;
    for (const f of Array.from(fileList)) {
      // #219: no client-side size cap — the upload streams to a blob store, so
      // big files are fine. The server enforces its own single-file limit and
      // rejects an over-size upload (handled below).
      // Preserve folder structure when a directory was picked.
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
      const path = `${targetDir}/${rel}`.replace(/\/+/g, "/");
      if (existing.has(path) && !confirm(`${path} exists. Overwrite?`)) continue;
      try {
        await svc.writeFile(path, f);
      } catch (err) {
        // #245: a 507 is the workspace quota (out of space), distinct from the
        // 413 single-file size cap — say which so the fix is obvious.
        const status = (err as { status?: number }).status;
        alert(
          status === 507
            ? `${f.name} could not be uploaded — the workspace is out of space.`
            : `${f.name} could not be uploaded — it may exceed the size limit.`,
        );
        continue;
      }
      firstPath ??= path;
    }
    refresh();
    if (firstPath) onOpen(firstPath, { preview: false });
  };

  // #364: upload OS files/folders dropped from outside (not an internal reorder) into
  // `dir` — recursing dropped folders so their subtree keeps its shape.
  const uploadExternal = async (dt: DataTransfer, dir: string) => {
    const incoming = await readTransferEntries(dt);
    if (incoming.length) await upload(incoming, dir);
  };

  // #364: paste files/images into `dir`. Plain-text pastes carry no files → no-op, so
  // pasting into a focused editor is unaffected. Images get a synthesized name.
  const pasteInto = (e: React.ClipboardEvent, dir: string) => {
    const { images, files: pasted } = extractClipboardFiles(e.clipboardData, Date.now());
    const all = [...images, ...pasted];
    if (all.length) {
      e.preventDefault();
      void upload(all, dir);
    }
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
    await svc.deleteFile(dest);
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
        if (copy) await svc.copyFile(srcPath, destBase);
        else await svc.moveFile(srcPath, destBase);
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
        await svc.writeFile(path, "");
        refresh();
        onOpen(path, { preview: false });
      } else {
        // Real, honest folder — no .keep placeholder.
        await svc.mkdir(path);
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
      await svc.moveFile(node.path, next);
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
    for (const p of tops) await svc.deleteFile(p);
    setSel({ selected: [], anchor: null });
    refresh();
  };

  // #247: native-anchor download (the browser streams straight to disk). The
  // `download` attr names the saved file so a KB blob isn't saved as its hash.
  const triggerDownload = (url: string, filename: string) => {
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  // A file downloads its bytes directly; a folder is zipped server-side first
  // (prepare → stream), entries rooted at the folder.
  const downloadNode = async (node: TreeNode) => {
    if (node.isDir) {
      const prep = await svc.prepareDirDownload(node.path);
      triggerDownload(svc.dirDownloadUrl(prep.download_id, node.path), prep.filename);
    } else {
      triggerDownload(svc.fileDownloadUrl(node.path), basename(node.path));
    }
  };
  // The whole tree (root, prefix ""). Confirmed first since it can be a lot of
  // files; per-folder / per-file downloads are explicit and skip the prompt.
  const downloadAll = async () => {
    const n = files.length;
    const choice = await dialog.confirm({
      title: "Download all",
      body: `Download all ${n} file${n === 1 ? "" : "s"} as a zip?`,
      actions: [
        { id: "download", label: "Download" },
        { id: "cancel", label: "Cancel" },
      ],
    });
    if (choice !== "download") return;
    const prep = await svc.prepareDirDownload("");
    triggerDownload(svc.dirDownloadUrl(prep.download_id, ""), prep.filename);
  };

  return (
    <div>
      {/* "Files" header with the three actions: new file, new folder, upload.
          #346: sticky-at-top so the title + action icons stay visible however
          far the tree scrolls inside its container. The opaque background is
          container-tunable via --filetree-header-bg (KB IDEs sit on --white,
          the workspace sidebar on --paper) so nothing is a see-through smear. */}
      <div
        data-testid="file-tree-header"
        style={{
          position: "sticky",
          top: 0,
          // #460 P3: sit above rows/badges so a scrolled row can never paint
          // over the header, and carry the top spacing INSIDE this opaque sticky
          // box (the scroll container no longer pads its top) so no row peeks
          // through a transparent band above the header.
          zIndex: 2,
          background: "var(--filetree-header-bg, var(--paper))",
          display: "flex",
          alignItems: "center",
          padding: "10px 10px 4px 14px",
        }}
      >
        <span className="caps" style={{ flex: searchable ? undefined : 1 }}>
          Files
        </span>
        {searchable && (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: 4,
              margin: "0 6px",
              padding: "1px 6px",
              minWidth: 0,
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-chip, 6px)",
              background: "var(--white)",
            }}
          >
            <Icon name="search" size={12} color="var(--text-paper-d)" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setQuery("");
              }}
              placeholder="Filter files…"
              aria-label="Filter files"
              style={{
                flex: 1,
                minWidth: 0,
                border: "none",
                outline: "none",
                background: "transparent",
                fontSize: pxToRem(12),
                color: "var(--text-paper)",
              }}
            />
            {query && (
              <button
                type="button"
                aria-label="clear filter"
                title="Clear filter"
                onClick={() => setQuery("")}
                style={{ color: "var(--text-paper-d)", padding: 0, lineHeight: 1 }}
              >
                <Icon name="x" size={12} />
              </button>
            )}
          </div>
        )}
        <button
          type="button"
          title="Refresh files"
          aria-label="refresh files"
          onClick={refresh}
          style={{ color: "var(--text-paper-d)", padding: 2 }}
        >
          <Icon name="refresh" size={13} />
        </button>
        {caps.download && (
          <button
            type="button"
            title="Download all"
            aria-label="download all"
            onClick={() => void downloadAll()}
            style={{ color: "var(--text-paper-d)", padding: 2 }}
          >
            <Icon name="download" size={13} />
          </button>
        )}
        {caps.create && (
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
        )}
        {caps.folders && (
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
        )}
        {caps.upload && (
        <div style={{ position: "relative" }}>
          <button
            type="button"
            title={createDir ? `Upload to ${createDir}/` : "Upload files or a folder"}
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
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={(e) => {
            void upload(e.target.files, uploadDirRef.current ?? createDir);
            uploadDirRef.current = null;
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
            void upload(e.target.files, uploadDirRef.current ?? createDir);
            uploadDirRef.current = null;
            e.target.value = "";
          }}
          style={{ display: "none" }}
        />
      </div>

      {/* Tree body — also the root drop zone (move/copy to root). Only the
          genuine empty area highlights, so dragging a file no longer tints
          the whole tree: we ignore dragover that bubbled up from a row. */}
      <div
        data-testid="file-tree-body"
        // #364: also the paste target — files/images land in the anchored folder; a
        // plain-text paste is a no-op (so pasting in a focused editor is untouched).
        onPaste={(e) => pasteInto(e, createDir)}
        onDragOver={(e) => {
          if (
            e.target === e.currentTarget &&
            (e.dataTransfer.types.includes("application/x-rca-file") || isExternalDrag(e))
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
            return;
          }
          // #364: OS files/folders dropped on the empty area → upload into anchored dir.
          if (isExternalDrag(e)) {
            e.preventDefault();
            void uploadExternal(e.dataTransfer, createDir);
          }
        }}
        tabIndex={0}
        onKeyDown={(e) => {
          if (sel.selected.length === 0) return;
          if (caps.delete && (e.key === "Delete" || e.key === "Backspace")) {
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
          <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
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
            caps={caps}
            decorate={decorate}
            activePath={activePath}
            selectedSet={selectedSet}
            multi={sel.selected.length > 1}
            select={select}
            collapsed={collapsed}
            forceOpen={expand}
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
            onUploadExternal={(dt, dir) => void uploadExternal(dt, dir)}
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
          caps={caps}
          multi={selectedSet.has(menu.node.path) && sel.selected.length > 1}
          canSplit={!!onOpenInSplit && !menu.node.isDir}
          onClose={() => setMenu(null)}
          onNewFile={(dir) => setCreating({ kind: "file", dir })}
          onNewFolder={(dir) => setCreating({ kind: "folder", dir })}
          onUploadHere={(dir, kind) => {
            uploadDirRef.current = dir;
            (kind === "folder" ? folderInputRef : fileInputRef).current?.click();
          }}
          onRename={(n) => setRenaming(n.path)}
          onDelete={(n) => void deletePaths(targetsFor(n.path))}
          onReindex={onReindex ? (n) => onReindex(targetsFor(n.path)) : undefined}
          onCopyPath={(p) => void navigator.clipboard?.writeText(p)}
          onDownload={(n) => void downloadNode(n)}
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
          fontSize: pxToRem(12),
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
  caps,
  decorate,
  activePath,
  selectedSet,
  multi,
  select,
  collapsed,
  forceOpen,
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
  onUploadExternal,
  readDragFile,
}: {
  node: TreeNode;
  depth: number;
  caps: FileCaps;
  decorate?: (path: string) => React.ReactNode;
  activePath: string | null;
  selectedSet: Set<string>;
  multi: boolean;
  select?: SelectMode;
  collapsed: ReturnType<typeof usePersistentSet>;
  /** #402: dirs the active filter forces open, overriding `collapsed`. */
  forceOpen: ReadonlySet<string>;
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
  onUploadExternal: (dt: DataTransfer, dir: string) => void;
  readDragFile: (e: React.DragEvent) => { paths: string[] } | null;
}) {
  const indent = 8 + depth * 12;
  // A filter match forces this dir open even if the user had collapsed it (#402).
  const isCollapsed = collapsed.has(node.path) && !forceOpen.has(node.path);
  const [dropOver, setDropOver] = useState(false);
  const [dragging, setDragging] = useState(false);
  // Drag move/copy only when the service supports relocation (KB v1 doesn't).
  const canDrag = caps.move || caps.copy;

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
    const fstate = select ? folderState(node, select.selected) : "unchecked";
    return (
      <div>
        {select ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              width: "100%",
              padding: `4px 14px 4px ${indent}px`,
              fontSize: pxToRem(12),
              color: "var(--text-paper-d)",
            }}
          >
            <input
              type="checkbox"
              aria-label={node.name}
              checked={fstate === "checked"}
              ref={(el) => {
                if (el) el.indeterminate = fstate === "indeterminate";
              }}
              onChange={() => select.onChange(toggleSubtree(node, select.selected))}
            />
            <button
              type="button"
              onClick={() => collapsed.toggle(node.path)}
              aria-label={`${isCollapsed ? "expand" : "collapse"} ${node.name}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                flex: 1,
                minWidth: 0,
                textAlign: "left",
                background: "transparent",
                color: "inherit",
                padding: 0,
                cursor: "pointer",
              }}
            >
              <Icon name={isCollapsed ? "chev_r" : "chev_d"} size={13} />
              <span>{node.name}</span>
            </button>
          </div>
        ) : (
        <button
          type="button"
          draggable={canDrag}
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
          // Drop target: move/copy an internally-dragged file/folder into this folder,
          // or (#364) upload OS files/folders dropped from outside into it.
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("application/x-rca-file") || isExternalDrag(e)) {
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
              return;
            }
            if (isExternalDrag(e)) {
              e.preventDefault();
              e.stopPropagation();
              onUploadExternal(e.dataTransfer, node.path);
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
            fontSize: pxToRem(12),
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
        )}
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
                caps={caps}
                decorate={decorate}
                activePath={activePath}
                selectedSet={selectedSet}
                multi={multi}
                select={select}
                collapsed={collapsed}
                forceOpen={forceOpen}
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
                onUploadExternal={onUploadExternal}
                readDragFile={readDragFile}
              />
            ))}
          </>
        )}
      </div>
    );
  }

  // Select mode: a leaf file is a checkbox row (no open/drag/context menu).
  if (select) {
    const checked = select.selected.has(node.path);
    return (
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          padding: `4px 14px 4px ${indent}px`,
          cursor: "pointer",
          fontSize: pxToRem(12),
          color: "var(--text-paper)",
          background: checked ? "var(--paper-2)" : "transparent",
        }}
      >
        <input
          type="checkbox"
          checked={checked}
          aria-label={node.name}
          onChange={() => select.onChange(toggleSubtree(node, select.selected))}
        />
        <span
          style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {basename(node.path)}
        </span>
        {decorate?.(node.path)}
      </label>
    );
  }

  const active = node.path === activePath;
  const selected = selectedSet.has(node.path);
  return (
    <button
      type="button"
      draggable={canDrag}
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
        canDrag
          ? multi && selected
            ? "Drag to move all selected · Ctrl/⌘ to copy"
            : "Drag onto a folder to move · Ctrl/⌘-drag to copy · drag into a pane to open there"
          : basename(node.path)
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
        fontSize: pxToRem(12),
        opacity: dragging ? 0.4 : 1,
      }}
    >
      <Icon name="file" size={13} color="var(--text-paper-d)" />
      <span
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {basename(node.path)}
      </span>
      {decorate?.(node.path)}
    </button>
  );
}

function TreeContextMenu({
  node,
  x,
  y,
  caps,
  multi,
  canSplit,
  onClose,
  onNewFile,
  onNewFolder,
  onUploadHere,
  onRename,
  onDelete,
  onReindex,
  onCopyPath,
  onDownload,
  onOpenInSplit,
}: {
  node: TreeNode;
  x: number;
  y: number;
  caps: FileCaps;
  /** The right-clicked node is part of a multi-selection (>1) — only show
   * actions that act on the whole selection (#98). */
  multi: boolean;
  canSplit: boolean;
  onClose: () => void;
  onNewFile: (dir: string) => void;
  onNewFolder: (dir: string) => void;
  onUploadHere: (dir: string, kind: "file" | "folder") => void;
  onRename: (n: TreeNode) => void;
  onDelete: (n: TreeNode) => void;
  onReindex?: (n: TreeNode) => void;
  onCopyPath: (p: string) => void;
  /** #247: download this node — a file streams its bytes, a folder a zip. */
  onDownload: (n: TreeNode) => void;
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
        fontSize: pxToRem(12),
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
  // Keep the menu on-screen: when the click is near the bottom / right edge,
  // anchor from the opposite side so it opens upward / leftward instead of
  // running off the viewport (#99). A generous estimate is fine — flipping a
  // touch early just opens the menu above/left of the cursor.
  const EST_H = 300;
  const EST_W = 200;
  const vstyle: React.CSSProperties =
    y + EST_H > window.innerHeight ? { bottom: window.innerHeight - y } : { top: y };
  const hstyle: React.CSSProperties =
    x + EST_W > window.innerWidth ? { right: window.innerWidth - x } : { left: x };
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 80 }} />
      <div
        data-testid="tree-context-menu"
        style={{
          position: "fixed",
          ...vstyle,
          ...hstyle,
          zIndex: 81,
          minWidth: 190,
          background: "var(--white)",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-card)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.16)",
          padding: "4px 0",
        }}
      >
        {/* A multi-selection only exposes actions that span the whole
            selection — single-target ops (rename, new, copy path, open-to-side)
            would be ambiguous, so they're hidden (#98). */}
        {multi ? (
          <>
            {onReindex && item("Reindex", () => onReindex(node))}
            {caps.delete && item("Delete", () => onDelete(node))}
          </>
        ) : (
          /* Groups (create · mutate · copy) gated by caps; seps only between two
             non-empty groups, so KB (delete-only) shows no stray rules. */
          (() => {
          const topGroup =
            (!node.isDir && canSplit && !!onOpenInSplit) ||
            caps.create ||
            caps.folders ||
            caps.upload;
          const mutateGroup = caps.move || caps.delete || !!onReindex;
          return (
            <>
              {!node.isDir && canSplit && onOpenInSplit && item("Open to the side", () => onOpenInSplit(node.path))}
              {caps.create && item("New file…", () => onNewFile(dir))}
              {caps.folders && item("New folder…", () => onNewFolder(dir))}
              {caps.upload && item("Upload files here…", () => onUploadHere(dir, "file"))}
              {caps.upload && item("Upload folder here…", () => onUploadHere(dir, "folder"))}
              {topGroup && mutateGroup && sep}
              {onReindex && item("Reindex", () => onReindex(node))}
              {caps.move && item("Rename…", () => onRename(node))}
              {caps.delete && item("Delete", () => onDelete(node))}
              {(topGroup || mutateGroup) && sep}
              {item("Copy path", () => onCopyPath(node.path))}
              {caps.download && item("Download", () => onDownload(node))}
            </>
          );
          })()
        )}
      </div>
    </>
  );
}
