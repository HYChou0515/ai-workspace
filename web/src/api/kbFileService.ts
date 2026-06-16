/**
 * kbFileService — a `FileService` over a KB collection's documents, so the
 * shared file-tree + renderers + editor (the investigation IDE shell, #87 P1)
 * works over KB docs too.
 *
 * Everything is specstar-native auto-CRUD — no custom KB edit endpoint:
 *   - read  = GET /source-doc/{id} (→ content.file_id) + GET .../blobs/{file_id}
 *   - write = POST /blobs/upload (immutable content-addressed blob) then
 *             CAS-PATCH /source-doc/{id} (merge-patch the `content` reference
 *             under If-Match: <revision_id>, retry on 412). A SourceDoc patch
 *             event handler (P2) auto-enqueues the reindex.
 *   - delete = the existing KB route (cascades chunks + un-folds the wiki).
 *
 * v1 hides move / copy / folders (caps) — KB docs are a flat, per-upload path
 * space; rename/move lands in a later pass.
 */

import { decodeBytes } from "./encoding";
import type { FileCaps, FileService } from "./fileService";
import { API_BASE, apiFetch } from "./http";
import type { KbApi, KbDocument } from "./kb";
import type { FileContent, FileInfo } from "./types";

const KB_CAPS: FileCaps = {
  write: true, // edit an existing doc's content
  create: true, // a new (empty) doc — uploaded on first save
  upload: true,
  delete: true,
  move: true, // rename / move (re-keys the doc — see the BE move route)
  copy: true,
  folders: true, // a folder is a hidden .gitkeep placeholder
};

function basename(path: string): string {
  const clean = path.replace(/\/+$/, "");
  return clean.slice(clean.lastIndexOf("/") + 1) || clean;
}

/** Normalise a path to a single leading-slash, `.`/`..`-resolved form, so doc
 * paths and resolved refs compare regardless of how they were stored. This is
 * the tree's canonical form: real uploads store relative paths (no leading
 * slash), the tree (and the investigation IDE it shares) speaks leading-slash —
 * normalising here is the single FE boundary that reconciles the two (#87). */
export function normPath(path: string): string {
  const stack: string[] = [];
  for (const seg of path.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") stack.pop();
    else stack.push(seg);
  }
  return "/" + stack.join("/");
}

/** Resolve a markdown ref against the doc it appears in: an absolute path is
 * collection-root; anything else is relative to the doc's directory. */
function resolveRefPath(fromPath: string, src: string): string {
  if (src.startsWith("/")) return normPath(src);
  const dir = fromPath.replace(/[^/]*$/, ""); // keep trailing slash
  return normPath(dir + src);
}

type DocEnvelope = {
  data: { content: { file_id: string; content_type: string; size: number } };
  revision_info: { revision_id: string };
};

/**
 * Build a FileService bound to one collection. `docs` is the collection's
 * current document list (the shell fetches it and feeds the tree); the service
 * resolves a tree path → its SourceDoc id through it. `onChanged` re-fetches
 * that list after a mutation. `kb` supplies the cascade-aware delete.
 */
export function kbFileService(
  collectionId: string,
  docs: readonly KbDocument[],
  kb: Pick<KbApi, "deleteDocument" | "uploadDocument" | "moveDocument">,
  onChanged?: () => void,
): FileService {
  // Indexed by the canonical (leading-slash) path: a doc stored relative
  // ("mydir/x.md") and the tree's path ("/mydir/x.md") resolve to one entry, so
  // every op keys off the same form whatever way the doc was originally stored.
  const byPath = new Map(docs.map((d) => [normPath(d.path), d]));

  const docFor = (path: string): KbDocument | undefined => byPath.get(normPath(path));

  const docIdFor = (path: string): string => {
    const doc = docFor(path);
    if (!doc) throw new Error(`unknown KB document: ${path}`);
    return doc.resource_id;
  };

  // Every doc beneath a folder path (incl the hidden .gitkeep) — KB has no
  // atomic subtree op, so folder move/copy/delete fan out over these.
  const docsUnder = (dir: string): KbDocument[] => {
    const prefix = normPath(dir) + "/";
    return docs.filter((d) => normPath(d.path).startsWith(prefix));
  };
  // Re-root a descendant's path from under `from` to under `to`.
  const reroot = (path: string, from: string, to: string): string =>
    normPath(to) + normPath(path).slice(normPath(from).length);

  const getEnvelope = async (docId: string): Promise<DocEnvelope> => {
    const resp = await apiFetch(`/source-doc/${encodeURIComponent(docId)}`);
    if (!resp.ok) throw new Error(`read document failed: ${resp.status}`);
    return (await resp.json()) as DocEnvelope;
  };

  return {
    scopeId: `kb:${collectionId}`,
    caps: KB_CAPS,

    listFiles: async (): Promise<FileInfo[]> =>
      docs.map((d) => ({ path: normPath(d.path), size: d.size ?? 0 })),
    listDirs: async (): Promise<string[]> => [],

    async readFile(path: string): Promise<FileContent> {
      const docId = docIdFor(path);
      // 1. resolve the doc's current content blob id, 2. fetch its raw bytes.
      // Raw (not the render projection) so every text type round-trips on edit
      // and the renderers see real bytes (csv parses csv, an image rebuilds).
      const env = await getEnvelope(docId);
      const fileId = env.data.content.file_id;
      const resp = await apiFetch(
        `/source-doc/${encodeURIComponent(docId)}/blobs/${encodeURIComponent(fileId)}`,
      );
      if (!resp.ok) throw new Error(`read ${path} failed: ${resp.status}`);
      const bytes = new Uint8Array(await resp.arrayBuffer());
      const { text, encoding } = decodeBytes(bytes);
      return { kind: "text", path, text, size: bytes.length, encoding };
    },

    async writeFile(path: string, body: string | Blob | ArrayBuffer): Promise<void> {
      // Save via the path-keyed ingest route — for a new path it mints the doc,
      // for an existing one it overwrites IN PLACE (a collection is a shared
      // drive: same path = one doc, last write wins) and re-indexes the edit.
      //
      // We deliberately do NOT do an If-Match CAS PATCH here: a SourceDoc's
      // revision id embeds its doc id, which uses '∕' (U+2215, a non-ASCII
      // slash) — and HTTP headers are latin-1 only, so `If-Match: <revision>`
      // either throws in the browser or arrives mangled, making the CAS fail
      // every time (the PATCH never even lands). Re-ingest sidesteps the header.
      const file = body instanceof File ? body : new File([body], basename(path));
      await kb.uploadDocument(collectionId, file, path);
      onChanged?.();
    },

    async deleteFile(path: string): Promise<void> {
      const exact = docFor(path);
      if (exact) {
        await kb.deleteDocument(exact.resource_id);
        onChanged?.();
        return;
      }
      // A folder isn't a doc — delete every descendant (incl .gitkeep).
      const under = docsUnder(path);
      if (under.length === 0) throw new Error(`unknown KB document: ${path}`);
      for (const d of under) await kb.deleteDocument(d.resource_id);
      onChanged?.();
    },

    // Rename / move re-keys the doc (the id encodes its path): the BE move route
    // re-creates it at the new path preserving created_by. NOTE: any existing
    // citation to the old doc dangles, unavoidable while the id encodes the path.
    async moveFile(from: string, to: string): Promise<void> {
      const exact = docFor(from);
      if (exact) {
        await kb.moveDocument(exact.resource_id, to);
        onChanged?.();
        return;
      }
      // A folder isn't a doc — fan the move out over every descendant, remapping
      // each path under the new folder (the BE move route is per-doc).
      const under = docsUnder(from);
      if (under.length === 0) throw new Error(`unknown KB document: ${from}`);
      for (const d of under) await kb.moveDocument(d.resource_id, reroot(d.path, from, to));
      onChanged?.();
    },

    // Copy = read the source bytes and upload them as a fresh doc at `to`.
    async copyFile(from: string, to: string): Promise<void> {
      const copyOne = async (doc: KbDocument, dest: string): Promise<void> => {
        const env = await getEnvelope(doc.resource_id);
        const fileId = env.data.content.file_id;
        const resp = await apiFetch(
          `/source-doc/${encodeURIComponent(doc.resource_id)}/blobs/${encodeURIComponent(fileId)}`,
        );
        if (!resp.ok) throw new Error(`copy ${doc.path} failed: ${resp.status}`);
        const bytes = await resp.arrayBuffer();
        const file = new File([bytes], basename(dest), { type: env.data.content.content_type });
        await kb.uploadDocument(collectionId, file, dest);
      };
      const exact = docFor(from);
      if (exact) {
        await copyOne(exact, to);
        onChanged?.();
        return;
      }
      // Folder copy: fan out over every descendant under the new path.
      const under = docsUnder(from);
      if (under.length === 0) throw new Error(`unknown KB document: ${from}`);
      for (const d of under) await copyOne(d, reroot(d.path, from, to));
      onChanged?.();
    },

    // KB has no empty folders — persist a hidden `.gitkeep` so the folder shows
    // (filtered from the tree, like the wiki's placeholders).
    async mkdir(path: string): Promise<void> {
      const keep = new File(["\n"], ".gitkeep", { type: "text/plain" });
      await kb.uploadDocument(collectionId, keep, `${path.replace(/\/+$/, "")}/.gitkeep`);
      onChanged?.();
    },

    // KB has no out-of-band store to mirror; "refresh" just re-reads the list.
    async refreshFiles(): Promise<void> {
      onChanged?.();
    },

    // Resolve a markdown ref to a sibling doc's content blob (the old full-page
    // viewer's behaviour). Absolute URLs / fragments / protocol-relative refs
    // pass through; a relative ref resolves doc-relative to a sibling SourceDoc
    // and becomes its `/source-doc/{id}/blobs/{file_id}` URL. Unknown sibling →
    // left as-is (a broken-image marker beats a wrong URL).
    fileUrl: (src, fromPath) => {
      if (!src) return "";
      if (/^(?:[a-z][a-z0-9+.-]*:|#|\/\/)/i.test(src)) return src;
      const target = resolveRefPath(fromPath ?? "/", src);
      const sibling = byPath.get(target);
      if (!sibling || !sibling.file_id) return src;
      return `${API_BASE}/source-doc/${encodeURIComponent(sibling.resource_id)}/blobs/${encodeURIComponent(sibling.file_id)}`;
    },
  };
}
