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
import { API_BASE } from "./http";
import { qk } from "./queryKeys";
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
};

export type FileService = {
  /** Stable id for query-key scoping + tree-collapse persistence. */
  readonly scopeId: string;
  readonly caps: FileCaps;
  listFiles(prefix?: string): Promise<FileInfo[]>;
  listDirs(): Promise<string[]>;
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
   * URL. `fromPath` is the doc the ref appears in, so a service can resolve
   * doc-relative refs (KB sibling docs); the investigation service ignores it
   * and treats refs as workspace-root-relative. */
  fileUrl(src: string | undefined, fromPath?: string): string;
};

// ── investigation binding (existing behaviour, just scoped) ────────────────
export function investigationFileService(slug: string, investigationId: string): FileService {
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
    },
    listFiles: (prefix) => api.listFiles(slug, investigationId, prefix),
    listDirs: () => api.listDirs(slug, investigationId),
    readFile: (path) => api.readFile(slug, investigationId, path),
    writeFile: (path, body) => api.writeFile(slug, investigationId, path, body),
    deleteFile: (path) => api.deleteFile(slug, investigationId, path),
    moveFile: (from, to) => api.moveFile(slug, investigationId, from, to),
    copyFile: (from, to) => api.copyFile(slug, investigationId, from, to),
    mkdir: (path) => api.mkdir(slug, investigationId, path),
    refreshFiles: () => api.refreshFiles(slug, investigationId),
    fileUrl: (src) =>
      resolveServiceUrl(
        `a/${encodeURIComponent(slug)}/items/${encodeURIComponent(investigationId)}/files`,
        src,
      ),
  };
}

/** Resolve a workspace-relative ref to `{API_BASE}/{base}/{path}`; pass through
 * absolute URLs / fragments / protocol-relative refs untouched. Shared by every
 * service's `fileUrl` (the investigation file route, the KB blob route, …). */
export function resolveServiceUrl(base: string, src: string | undefined): string {
  if (!src) return "";
  if (/^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/i.test(src)) return src;
  const cleaned = src.replace(/^\.\//, "").replace(/^\/+/, "");
  const path = cleaned.split("/").map(encodeURIComponent).join("/");
  return `${API_BASE}/${base}/${path}`;
}

// ── React context ──────────────────────────────────────────────────────────
const FileServiceContext = createContext<FileService | null>(null);
export const FileServiceProvider = FileServiceContext.Provider;

export function useFileService(): FileService {
  const svc = useContext(FileServiceContext);
  if (!svc) throw new Error("useFileService must be used within a <FileServiceProvider>");
  return svc;
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
      const [items, dirs] = await Promise.all([svc.listFiles(), svc.listDirs()]);
      return { items, dirs };
    },
  });
  const refresh = () => void q.refetch();
  if (q.isPending) return { kind: "loading" };
  if (q.isError) return { kind: "error", error: q.error, refresh };
  return { kind: "ready", items: q.data.items, dirs: q.data.dirs, refresh };
}
