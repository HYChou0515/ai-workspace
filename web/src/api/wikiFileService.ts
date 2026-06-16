/**
 * wikiFileService — a `FileService` over a collection's LLM wiki pages, so the
 * shared file-tree IDE (FileTree + renderers + editor) edits the wiki like any
 * other filesystem (#D).
 *
 * Unlike the KB source docs, wiki pages are plain markdown in the WikiFileStore,
 * keyed by a leading-slash path ("/index.md", "/entities/x.md") — the same form
 * the tree speaks, so there's no relative/leading-slash reconciliation. The wiki
 * is AUTHORED, never uploaded into (caps.upload = false); a collection is a
 * shared drive so writes are last-write-wins (the maintainer may later revise a
 * hand-edited page). Editing needs no reindex — the wiki reader reads pages live.
 */

import type { FileCaps, FileService } from "./fileService";
import { normPath } from "./kbFileService";
import type { KbApi } from "./kb";
import type { FileContent, FileInfo } from "./types";

const WIKI_CAPS: FileCaps = {
  write: true,
  create: true,
  upload: false, // the wiki is authored in place, not uploaded into
  delete: true,
  move: true,
  copy: true,
  folders: true,
};

type WikiKb = Pick<KbApi, "getWikiPage" | "writeWikiPage" | "moveWikiPage" | "deleteWikiPage">;

/**
 * Build a FileService bound to one collection's wiki. `pages` is the current
 * page-path list (the shell feeds the tree); folder ops fan out over it since
 * the BE routes are per-page. `onChanged` re-fetches after a mutation.
 */
export function wikiFileService(
  collectionId: string,
  pages: readonly string[],
  kb: WikiKb,
  onChanged?: () => void,
): FileService {
  const known = pages.map(normPath);
  // Every page beneath a folder path — folder move/copy/delete fan out over it.
  const under = (dir: string): string[] => {
    const prefix = normPath(dir) + "/";
    return known.filter((p) => p.startsWith(prefix));
  };
  const reroot = (path: string, from: string, to: string): string =>
    normPath(to) + normPath(path).slice(normPath(from).length);
  const isPage = (path: string): boolean => known.includes(normPath(path));

  return {
    scopeId: `wiki:${collectionId}`,
    caps: WIKI_CAPS,

    listFiles: async (): Promise<FileInfo[]> => pages.map((p) => ({ path: normPath(p), size: 0 })),
    listDirs: async (): Promise<string[]> => [],

    async readFile(path: string): Promise<FileContent> {
      const { content } = await kb.getWikiPage(collectionId, normPath(path));
      return { kind: "text", path, text: content, size: content.length, encoding: "utf-8" };
    },

    async writeFile(path: string, body: string | Blob | ArrayBuffer): Promise<void> {
      const content = typeof body === "string" ? body : await new Blob([body]).text();
      await kb.writeWikiPage(collectionId, normPath(path), content);
      onChanged?.();
    },

    async moveFile(from: string, to: string): Promise<void> {
      if (isPage(from)) {
        await kb.moveWikiPage(collectionId, normPath(from), normPath(to));
      } else {
        // A folder isn't a page — fan the move out over every descendant.
        for (const p of under(from)) await kb.moveWikiPage(collectionId, p, reroot(p, from, to));
      }
      onChanged?.();
    },

    async copyFile(from: string, to: string): Promise<void> {
      const copyOne = async (src: string, dest: string): Promise<void> => {
        const { content } = await kb.getWikiPage(collectionId, src);
        await kb.writeWikiPage(collectionId, dest, content);
      };
      if (isPage(from)) {
        await copyOne(normPath(from), normPath(to));
      } else {
        for (const p of under(from)) await copyOne(p, reroot(p, from, to));
      }
      onChanged?.();
    },

    async deleteFile(path: string): Promise<void> {
      if (isPage(path)) {
        await kb.deleteWikiPage(collectionId, normPath(path));
      } else {
        for (const p of under(path)) await kb.deleteWikiPage(collectionId, p);
      }
      onChanged?.();
    },

    // The wiki has no empty folders — persist a hidden `.gitkeep` so a freshly
    // created folder shows (the wiki tree filters it out, like the doc IDE).
    async mkdir(path: string): Promise<void> {
      await kb.writeWikiPage(collectionId, `${normPath(path)}/.gitkeep`, "");
      onChanged?.();
    },

    // The wiki reader reads pages live; nothing to mirror on refresh.
    async refreshFiles(): Promise<void> {
      onChanged?.();
    },

    // Wiki markdown refs ([[wikilink]] / Sources) are resolved by the wiki
    // preview itself, not as file URLs; nothing to resolve here.
    fileUrl: (src) => src ?? "",
  };
}
