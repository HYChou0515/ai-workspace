/**
 * FileService — the backend-agnostic seam the file-tree IDE (FileTree +
 * renderers + editor) runs on, so the SAME shell works over investigation
 * workspace files OR a KB collection's documents (#87).
 *
 * The shell never imports the concrete API or knows an investigation id; it
 * reads a `FileService` from context. `investigationFileService(slug, id)` binds the
 * existing investigation file API; `kbFileService(collectionId)` (P3) binds the
 * KB document routes.
 */

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";

import { api } from "./index";
import { writeVerified } from "./writeVerified";
import { API_PREFIX, apiFetch } from "./http";
import type { DownloadPrepared } from "./kb";
import { qk } from "./queryKeys";
import { isExternalRef, resolveRefPath } from "./refPath";
import type { FileContent, FileInfo } from "./types";

/** What file operations a service supports — the tree hides actions it can't do
 * (KB v1 has no new-file / folders / move / copy; docs arrive by upload). */
export type FileCaps = {
  write: boolean; // edit + save an existing file's content
  create: boolean; // make a new (empty) file inline in the tree
  upload: boolean; // add files via the upload button
  delete: boolean;
  move: boolean; // rename / move
  copy: boolean;
  folders: boolean; // mkdir / empty folders
  download: boolean; // download a file (direct) or a folder/root (zip) — #247
};

export type FileService = {
  /** Stable id for query-key scoping + tree-collapse persistence. */
  readonly scopeId: string;
  readonly caps: FileCaps;
  listFiles(prefix?: string): Promise<FileInfo[]>;
  listDirs(): Promise<string[]>;
  /** Files AND folders from ONE backend traversal. `listFiles` + `listDirs` in
   * parallel walked the whole workspace twice for two halves of one answer, and
   * this hook shares a cache key with the shell's listing — so two hooks with
   * two different query functions were fetching the same thing. */
  listTree(): Promise<{ items: FileInfo[]; dirs: string[] }>;
  readFile(path: string): Promise<FileContent>;
  writeFile(path: string, body: string | Blob | ArrayBuffer): Promise<void>;
  deleteFile(path: string): Promise<void>;
  moveFile(from: string, to: string): Promise<void>;
  copyFile(from: string, to: string): Promise<void>;
  mkdir(path: string): Promise<void>;
  /** Force-sync any out-of-band changes before a read (investigation sandbox);
   * a no-op where there's nothing to mirror (KB). */
  refreshFiles(): Promise<void>;
  /** Resolve a markdown ref (`![](src)` image / `[](href)` link) to a browser
   * URL. `fromPath` is the document the ref appears in: a relative ref means
   * the file NEXT TO that document, an absolute one is tree-root. Every service
   * resolves by that one rule, so a document renders the same whichever tree it
   * was opened from. Omit `fromPath` only where there is no containing document
   * to resolve against (chat markdown) — then a ref is tree-root-relative. */
  fileUrl(src: string | undefined, fromPath?: string): string;
  /** #247: the URL a native `<a download>` points at to save ONE file verbatim
   * (KB → its content blob; investigation → the file route). The caller sets the
   * anchor's `download` to the basename so the saved name is clean. */
  fileDownloadUrl(path: string): string;
  /** #247: build a zip of the folder `prefix` (`""` = the whole tree) server-side
   * and return the handle to stream it. */
  prepareDirDownload(prefix: string): Promise<DownloadPrepared>;
  /** #247: the URL a native `<a download>` points at to stream a prepared folder
   * zip. `prefix` is echoed so the streamed file is named after the folder. */
  dirDownloadUrl(downloadId: string, prefix: string): string;
};

// ── investigation binding (existing behaviour, just scoped) ────────────────
export function investigationFileService(slug: string, investigationId: string): FileService {
  const filesBase = `a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files`;
  return {
    scopeId: investigationId, // matches existing qk.file/qk.files keys
    caps: {
      write: true,
      create: true,
      upload: true,
      delete: true,
      move: true,
      copy: true,
      folders: true,
      download: true,
    },
    listFiles: (prefix) => api.listFiles(slug, investigationId, prefix),
    listDirs: () => api.listDirs(slug, investigationId),
    listTree: async () => {
      const { files, dirs } = await api.getTree(slug, investigationId);
      return { items: files, dirs };
    },
    readFile: (path) => api.readFile(slug, investigationId, path),
    // #493: "did the response come back OK" and "are the bytes there" differ
    // exactly when the connection is cut AFTER the body was sent — and the
    // server has usually stored the file by then. Deciding that here means no
    // writer (file tree, attachments, skills/workflows/collections pickers, the
    // editor's save, both KB IDEs) can get it wrong by omission.
    writeFile: (path, body) =>
      writeVerified(
        () => api.writeFile(slug, investigationId, path, body),
        async () => {
          const all = await api.listFiles(slug, investigationId);
          return all.some((f) => f.path === path || f.path === `/${path.replace(/^\//, "")}`);
        },
      ),
    deleteFile: (path) => api.deleteFile(slug, investigationId, path),
    moveFile: (from, to) => api.moveFile(slug, investigationId, from, to),
    copyFile: (from, to) => api.copyFile(slug, investigationId, from, to),
    mkdir: (path) => api.mkdir(slug, investigationId, path),
    refreshFiles: () => api.refreshFiles(slug, investigationId),
    // A relative ref means the file next to the DOCUMENT it was written in, so
    // `![](./a.png)` in `/reports/r.md` is `/reports/a.png` — the same rule the
    // KB tree resolves by, and the one whoever wrote the markdown meant.
    // `fromPath` absent (chat markdown has no containing document) is the only
    // case that can still mean workspace-root-relative.
    fileUrl: (src, fromPath) =>
      !src || isExternalRef(src)
        ? resolveServiceUrl(filesBase, src)
        : resolveServiceUrl(filesBase, resolveRefPath(fromPath ?? "/", src)),
    fileDownloadUrl: (path) => resolveServiceUrl(filesBase, path),
    prepareDirDownload: async (prefix) => {
      const resp = await apiFetch(
        `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/download/prepare?prefix=${encodeURIComponent(prefix)}`,
        { method: "POST" },
      );
      if (!resp.ok) throw new Error(`prepare folder download failed: ${resp.status}`);
      return resp.json();
    },
    dirDownloadUrl: (downloadId, prefix) =>
      `${API_PREFIX}/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files/download/${encodeURIComponent(downloadId)}?prefix=${encodeURIComponent(prefix)}`,
  };
}

/** Resolve a workspace-relative ref to `{API_PREFIX}/{base}/{path}`; pass through
 * absolute URLs / fragments / protocol-relative refs untouched. Shared by every
 * service's `fileUrl` (the investigation file route, the KB blob route, …). */
export function resolveServiceUrl(base: string, src: string | undefined): string {
  if (!src) return "";
  if (isExternalRef(src)) return src;
  const cleaned = src.replace(/^\.\//, "").replace(/^\/+/, "");
  const path = cleaned.split("/").map(encodeURIComponent).join("/");
  return `${API_PREFIX}/${base}/${path}`;
}

// ── React context ──────────────────────────────────────────────────────────
const FileServiceContext = createContext<FileService | null>(null);
export const FileServiceProvider = FileServiceContext.Provider;

export function useFileService(): FileService {
  const svc = useContext(FileServiceContext);
  if (!svc) throw new Error("useFileService must be used within a <FileServiceProvider>");
  return svc;
}

/** The service in context, or `null` when there's no provider. The read-only
 * FileTree select mode (the card-gen picker, #415) runs without a writable
 * service — it feeds its own file list and never mutates — so it reads the
 * service optionally and falls back to a no-caps shell. */
export function useOptionalFileService(): FileService | null {
  return useContext(FileServiceContext);
}

// ── derived hooks (read from whichever service is in context) ──────────────
type FileListState =
  | { kind: "loading" }
  | { kind: "ready"; items: FileInfo[]; dirs: string[]; refresh: () => void }
  | { kind: "error"; error: Error; refresh: () => void };

/** The active service's file + dir listing, cached under `qk.files(scopeId)`
 * (so it shares the cache the shell's listing fills and `useRefreshFiles`
 * busts). The backend-agnostic twin of `useFiles(investigationId)`. */
export function useFileList(): FileListState {
  const svc = useFileService();
  const q = useQuery({
    queryKey: qk.files(svc.scopeId),
    queryFn: async () => {
      return await svc.listTree();
    },
  });
  const refresh = () => void q.refetch();
  if (q.isPending) return { kind: "loading" };
  if (q.isError) return { kind: "error", error: q.error, refresh };
  return { kind: "ready", items: q.data.items, dirs: q.data.dirs, refresh };
}
