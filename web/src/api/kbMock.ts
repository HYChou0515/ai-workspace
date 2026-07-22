/**
 * In-memory KB API for dev/tests (VITE_USE_MOCK=1). Mirrors the real client's
 * observable behavior: create/list collections, upload→list documents, chat
 * thread CRUD, and a scripted streaming turn whose persisted answer carries a
 * citation (so the UI's citation path is exercisable without a backend).
 */

import type { AgentEvent } from "../events";
import type { CollectionPermission } from "../lib/permission";
import type {
  KbApi,
  KbCardGenStatus,
  KbChatDetail,
  KbChatMessage,
  KbChatSummary,
  KbCollection,
  KbContextCard,
  KbDocChunk,
  KbDocQuestion,
  KbDocument,
  KbProposedCard,
  KbRenderedDoc,
  KbReviewCard,
  KbReviewQuestion,
  SendKbMessageArgs,
} from "./kb";

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${(++seq).toString(36)}`;

/** The mock's internal doc row keeps the open-a-document fields (#395 moved
 * them off the list wire) so renderDocument can serve them; listDocuments
 * strips them to mirror the real BE. */
type MockDoc = KbDocument & {
  quality_rationale?: string;
  quality_breakdown?: Record<string, number>;
  parser_guidance_override?: string;
};

const collections = new Map<string, KbCollection>();
/** #310: per-collection access state (absent ⇒ public with no grants). */
const collectionPerms = new Map<string, CollectionPermission>();
const docPerms = new Map<string, CollectionPermission>();

const defaultPermission = (): CollectionPermission => ({
  visibility: "public",
  read_meta: [],
  write_meta: [],
  read_content: [],
  add_content: [],
  edit_content: [],
  read_chat: [],
  converse: [],
  execute: [],
  use_terminal: [],
  change_permission: [],
});
const documents = new Map<string, MockDoc[]>();
const docChunks = new Map<string, KbDocChunk[]>();
const chats = new Map<string, KbChatDetail>();
// #357: monotonic "revision time" per chat, bumped on create / rename / send —
// stands in for specstar's updated_time so the list's recency sort is testable
// without wall-clock nondeterminism.
let chatClock = 1_000;
const chatStamps = new Map<string, number>();
const touchChat = (id: string) => chatStamps.set(id, (chatClock += 1));
// collectionId → its context cards (#106), keyed by collection like documents.
const contextCards = new Map<string, KbContextCard[]>();
// #377: the global doc-question inbox (id → question). Empty by default; the
// page tests drive the api directly.
const docQuestions = new Map<string, KbDocQuestion>();
// jobId → a 自動 context card generation run (#175). The mock completes the run
// synchronously (status "completed") with one proposal per selected document.
const cardGenJobs = new Map<
  string,
  {
    collectionId: string;
    status: KbCardGenStatus["status"];
    proposals: KbProposedCard[];
    resolved?: boolean; // #415: committed / dismissed → out of the 待審核 queue
  }
>();

/** Faithful mirror of the BE `norm()` — NFKC, casefold, collapse whitespace —
 * then deduped + sorted, so the mock's `norm_keys` look like production's. */
const normKey = (s: string) => s.normalize("NFKC").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
const deriveNormKeys = (keys: string[]) =>
  [...new Set(keys.map(normKey).filter(Boolean))].sort();
// collectionId → (page path → markdown). The LLM wiki, mocked.
const wikiPages = new Map<string, Map<string, string>>();
// collectionId → live build status (the "Updating…" UI polls this).
const wikiStatus = new Map<
  string,
  {
    building: boolean;
    total: number;
    done: number;
    current: string | null;
    phase: string | null;
    errors: number;
    last_error: string | null;
  }
>();

/** Path stem (basename without extension) — wiki link target. */
const stem = (path: string) => (path.split("/").pop() ?? path).replace(/\.[^.]+$/, "");

// Deterministic chunking for the mock: split into ~120-char windows so a small
// upload yields at least one chunk. Cited counts stay 0 (the mock doesn't feed
// citations back into chunks).
function synthChunks(docId: string, body: string): KbDocChunk[] {
  const size = 120;
  const out: KbDocChunk[] = [];
  for (let start = 0, i = 0; start < Math.max(body.length, 1); start += size, i++) {
    const end = Math.min(start + size, body.length);
    out.push({
      chunk_id: `${docId}#${i}`,
      seq: i,
      start,
      end,
      text: body.slice(start, end) || body,
      cited: 0,
    });
  }
  return out;
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

function summarize(chat: KbChatDetail): KbChatSummary {
  const firstUser = chat.messages.find((m) => m.role === "user" && m.content.trim());
  return {
    resource_id: chat.resource_id,
    title: chat.title,
    collection_ids: chat.collection_ids,
    message_count: chat.messages.length,
    owner: chat.owner ?? "default-user",
    shared_with: chat.shared_with ?? [],
    // #357: label an unnamed chat by its first user message.
    name_hint: firstUser ? firstUser.content.split(/\s+/).join(" ").slice(0, 60) : "",
    updated_ms: chatStamps.get(chat.resource_id) ?? null,
  };
}

export const mockKbApi: KbApi = {
  async getAgentConfig() {
    // Issue #32: array of picker entries. Single-entry default in the
    // mock; tests that need multiple can override mockKbApi.
    return [
      {
        name: "KB Agent",
        model: "ollama_chat/qwen3:14b",
        suggestions: [
          {
            label: "What does the knowledge base say about this?",
            prompt: "What does the knowledge base say about this?",
          },
          { label: "Find related past findings", prompt: "Find related past findings" },
        ],
      },
    ];
  },
  async listCollections() {
    // Recompute the card aggregates from the collection's documents.
    return [...collections.values()].map((c) => {
      const docs = documents.get(c.resource_id) ?? [];
      const size = docs.reduce((s, d) => s + (d.size ?? 0), 0);
      const updated = docs.reduce((m, d) => Math.max(m, d.updated_at ?? 0), c.updated_at);
      return { ...c, doc_count: docs.length, size, updated_at: updated };
    });
  },
  async createCollection(name, description = "", opts) {
    const c: KbCollection = {
      resource_id: nextId("col"),
      name,
      description,
      icon: "layers",
      cited: 0,
      doc_count: 0,
      size: 0,
      tokens: 0,
      updated_at: Date.now(),
      owner: "me",
      use_rag: opts?.useRag ?? true,
      use_wiki: opts?.useWiki ?? false,
      git_url: opts?.gitUrl ?? null,
      git_branch: opts?.gitBranch ?? null,
      git_last_sha: null,
      git_last_pulled_at: null,
      wiki_maintainer_guidance: "",
      wiki_reader_guidance: "",
      is_global: false,
      auto_digest: false,
    };
    collections.set(c.resource_id, c);
    return c;
  },
  async updateCollection(id, patch) {
    const c = collections.get(id);
    if (c) collections.set(id, { ...c, ...patch });
  },
  async deleteCollection(id) {
    collections.delete(id);
    documents.delete(id);
  },
  async reindexCollection(id, opts) {
    const list = documents.get(id) ?? [];
    documents.set(
      id,
      // `{ only: "failed" }` (issue #223) recovers just the `error` docs; the
      // whole-collection call (no opts) re-indexes everything.
      list.map((d) => (opts?.only === "failed" && d.status !== "error" ? d : { ...d, status: "ready" })),
    );
    const covered = opts?.only === "failed" ? list.filter((d) => d.status === "error").length : list.length;
    return { queued: true, documents: covered, status: "indexing" };
  },
  async listDocuments(collectionId, page) {
    const offset = page?.offset ?? 0;
    const limit = page?.limit ?? 50;
    // Mirror the BE's Query() bounds (kb_routes.py list_documents: limit ge=1
    // le=5000 since #395, offset ge=0) so a caller that oversteps fails here
    // too, instead of only in production. Without this the mock silently
    // masked #394's limit=1000 → 422 bug.
    if (limit < 1 || limit > 5000)
      throw new Error(`list documents: limit ${limit} out of range (1..5000)`);
    if (offset < 0) throw new Error(`list documents: offset ${offset} out of range`);
    const all = documents.get(collectionId) ?? [];
    // Mirror the BE sort (most-recent first) so paging looks the same as
    // production. Use updated_at when present; fall back to insertion order.
    const sorted = [...all].sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
    // #395: rows are metas-only on the real BE — the open-a-document fields
    // stay off the list wire.
    const items = sorted
      .slice(offset, offset + limit)
      .map(({ quality_rationale: _qr, parser_guidance_override: _pg, ...row }) => row);
    return {
      items,
      total: sorted.length,
      offset,
      limit,
      has_more: offset + items.length < sorted.length,
    };
  },
  async documentsStatus(collectionId) {
    const all = documents.get(collectionId) ?? [];
    const counts: Record<string, number> = {};
    for (const d of all) counts[d.status] = (counts[d.status] ?? 0) + 1;
    const runs: Record<string, { units_done: number; units_total: number }> = {};
    for (const d of all) {
      if (d.status === "indexing" && (d.units_total ?? 0) > 0) {
        runs[d.resource_id] = { units_done: d.units_done ?? 0, units_total: d.units_total ?? 0 };
      }
    }
    return {
      total: all.length,
      counts,
      runs,
      latest_ms: all.reduce((m, d) => Math.max(m, d.updated_at ?? 0), 0),
    };
  },
  async uploadDocument(collectionId, file, path) {
    const docPath = path ?? file.name;
    const id = `${collectionId}/me/${docPath}`;
    const list = documents.get(collectionId) ?? [];
    if (!list.some((d) => d.resource_id === id)) {
      const body = await file.text();
      const chunks = synthChunks(id, body);
      docChunks.set(id, chunks);
      list.push({
        resource_id: id,
        path: docPath,
        content_type: "text/markdown",
        created_by: "me",
        status: "ready",
        chunks: chunks.length,
        cited: 0,
        size: file.size || body.length,
        updated_at: Date.now(),
      });
    }
    documents.set(collectionId, list);
    return [id];
  },
  async listGraphProposals() {
    return [];
  },
  async decideGraphProposal() {},
  async listUploadChecks() {
    // #325: mirror the bundled Office hint so mock-mode pre-blocks too.
    return [
      {
        id: "office_encryption",
        extensions: [".pptx", ".xlsx", ".docx"],
        forbid_magic_hex: ["d0cf11e0a1b11ae1"],
        message_key: "kb.upload.blocked.unreadable",
      },
    ];
  },
  async prepareCollectionDownload(collectionId) {
    const c = collections.get(collectionId);
    const base = (c?.name ?? "collection").replace(/[^\w.\- ]+/g, "_") || "collection";
    return { download_id: "mockdl00000000000000000000000000", filename: `${base}.zip`, size: 1024 };
  },
  streamCollectionDownloadUrl(collectionId, downloadId) {
    return `/kb/collections/${collectionId}/download/${downloadId}`;
  },
  async prepareFolderDownload(_collectionId, prefix) {
    const folder = prefix.replace(/\/+$/, "").split("/").pop() || "download";
    return { download_id: "mockdl00000000000000000000000000", filename: `${folder}.zip`, size: 512 };
  },
  folderDownloadUrl(collectionId, downloadId, prefix) {
    return `/kb/collections/${collectionId}/folder-download/${downloadId}?prefix=${encodeURIComponent(prefix)}`;
  },
  async importCollectionNew(file) {
    // Simulate a new collection materialised from the uploaded zip, named after
    // the file (mirrors the BE manifest-less fallback the tests exercise).
    const name = file.name.replace(/\.zip$/i, "") || "imported";
    const c: KbCollection = {
      resource_id: nextId("col"),
      name,
      description: "",
      icon: "layers",
      cited: 0,
      doc_count: 0,
      size: 0,
      tokens: 0,
      updated_at: Date.now(),
      owner: "me",
      use_rag: true,
      use_wiki: false,
      wiki_maintainer_guidance: "",
      wiki_reader_guidance: "",
      is_global: false,
      auto_digest: false,
    };
    collections.set(c.resource_id, c);
    return { collection_id: c.resource_id, document_ids: [], status: "indexing" };
  },
  async importCollectionInto(collectionId, _file, _mode) {
    return { collection_id: collectionId, document_ids: [], status: "indexing" };
  },
  async probeFindability(body) {
    // #328/#356: a deterministic mock — the doc surfaces at #2, and any candidate
    // guidance "improves" it to #1, enough for tests that don't stub their own.
    const k = body.k ?? 5;
    const passage = (rank: number) => ({
      rank,
      in_top_k: rank <= k,
      text: `Mock passage for ${body.doc_id} (q: ${body.question}).`,
      location: "p.1",
    });
    return {
      top_k: k,
      depth: Math.max(50, k),
      before: { passages: [passage(2)], best_rank: 2 },
      after:
        body.guidance == null ? null : { passages: [passage(1)], best_rank: 1 },
    };
  },
  async *answerFindability(args) {
    // #356: a deterministic streamed mock answer that cites the (mock) passage.
    yield { type: "message_delta", text: `Mock answer for "${args.question}" `, reasoning: false };
    yield { type: "message_delta", text: "from this document [1].", reasoning: false };
    yield { type: "done" };
  },
  async setDocumentGuidance(documentId, guidance) {
    const collectionId = documentId.split("/")[0] ?? "";
    const list = documents.get(collectionId) ?? [];
    documents.set(
      collectionId,
      list.map((d) =>
        d.resource_id === documentId ? { ...d, parser_guidance_override: guidance } : d,
      ),
    );
  },
  async renderDocument(documentId): Promise<KbRenderedDoc> {
    const filename = documentId.split("/").pop() ?? documentId;
    const collection_id = documentId.split("/")[0] ?? "";
    const doc = (documents.get(collection_id) ?? []).find((d) => d.resource_id === documentId);
    const chunks = docChunks.get(documentId) ?? [];
    return {
      document_id: documentId,
      filename,
      collection_id,
      markdown: `# ${filename}\n\nMock document body for **${documentId}**.`,
      file_id: `blob-${documentId}`,
      content_type: doc?.content_type ?? "text/markdown",
      size: doc?.size ?? 0,
      chunks: doc?.chunks ?? chunks.length,
      cited: doc?.cited ?? 0,
      created_by: doc?.created_by ?? "me",
      updated_at: doc?.updated_at ?? Date.now(),
      status: doc?.status ?? "ready",
      // #395: the open-a-document fields ride the render response, not the row.
      quality_score: doc?.quality_score,
      quality_rationale: doc?.quality_rationale,
      parser_guidance_override: doc?.parser_guidance_override,
    };
  },
  async getSourceDocMeta(documentId) {
    const collection_id = documentId.split("/")[0] ?? "";
    const doc = (documents.get(collection_id) ?? []).find((d) => d.resource_id === documentId);
    // The card thumbnails read content_type + file_id off the envelope (#518).
    // A card's attachment ids are the opaque encode_doc_id tokens (not in the
    // paginated doc list), so synthesise an image blob from the decoded name so
    // thumbnails light up in mock mode / tests as they do against the real BE.
    let decoded = documentId;
    try {
      decoded = decodeURIComponent(documentId);
    } catch {
      /* keep raw */
    }
    const isImg = /\.(png|jpe?g|webp|gif)$/i.test(decoded);
    return {
      quality_score: doc?.quality_score,
      quality_rationale: doc?.quality_rationale,
      quality_breakdown: doc?.quality_breakdown,
      parser_guidance_override: doc?.parser_guidance_override,
      file_id: doc?.file_id ?? (isImg ? `mock-blob-${documentId}` : undefined),
      content_type: doc?.content_type ?? (isImg ? "image/png" : undefined),
    };
  },
  async getDocChunks(documentId) {
    return [...(docChunks.get(documentId) ?? [])].sort((a, b) => a.seq - b.seq);
  },
  async reindexDocument(documentId) {
    const collectionId = documentId.split("/")[0] ?? "";
    const list = documents.get(collectionId) ?? [];
    documents.set(
      collectionId,
      list.map((d) => (d.resource_id === documentId ? { ...d, status: "ready" } : d)),
    );
  },
  async deleteDocument(documentId) {
    const collectionId = documentId.split("/")[0] ?? "";
    documents.set(
      collectionId,
      (documents.get(collectionId) ?? []).filter((d) => d.resource_id !== documentId),
    );
    docChunks.delete(documentId);
  },
  async moveDocument(documentId, to) {
    const collectionId = documentId.split("/")[0] ?? "";
    const list = documents.get(collectionId) ?? [];
    const doc = list.find((d) => d.resource_id === documentId);
    if (!doc) return;
    // Re-key on the new path, preserving the creator (mock id = col/user/path).
    const newId = `${collectionId}/${doc.created_by}/${to.replace(/^\/+/, "")}`;
    documents.set(collectionId, [
      ...list.filter((d) => d.resource_id !== documentId),
      { ...doc, resource_id: newId, path: to },
    ]);
    const chunks = docChunks.get(documentId);
    if (chunks) {
      docChunks.set(newId, chunks);
      docChunks.delete(documentId);
    }
  },

  async listChats() {
    return [...chats.values()].map(summarize);
  },
  async createChat(title, collectionIds, excludedCollectionIds = []) {
    const chat: KbChatDetail = {
      resource_id: nextId("chat"),
      title, // #357: unnamed = "" (labelled by name_hint), not a literal "New chat"
      collection_ids: collectionIds,
      excluded_collection_ids: excludedCollectionIds,
      messages: [],
    };
    chats.set(chat.resource_id, chat);
    touchChat(chat.resource_id);
    return summarize(chat);
  },
  async getChat(chatId) {
    const chat = chats.get(chatId);
    if (!chat) throw new Error(`chat not found: ${chatId}`);
    return structuredClone(chat);
  },
  async renameChat(chatId, title) {
    const chat = chats.get(chatId);
    if (!chat) throw new Error(`chat not found: ${chatId}`);
    chat.title = title; // #357: "" reverts to the name_hint label
    touchChat(chatId);
    return summarize(chat);
  },
  async deleteChat(chatId) {
    chats.delete(chatId);
  },
  async getCollectionPermission(id) {
    return collectionPerms.get(id) ?? defaultPermission();
  },
  async setCollectionPermission(id, perm) {
    collectionPerms.set(id, perm);
    return { visibility: perm.visibility, notified: [] };
  },
  async setCollectionGlobal(id, isGlobal) {
    const c = collections.get(id);
    if (c) collections.set(id, { ...c, is_global: isGlobal });
    return { resource_id: id, is_global: isGlobal };
  },
  async requestCollectionAccess(id) {
    return { collection_id: id, requested: true, already_readable: false };
  },
  async getDocPermission(id) {
    return docPerms.get(id) ?? defaultPermission();
  },
  async setDocPermission(id, perm) {
    docPerms.set(id, perm);
    return { visibility: perm.visibility, notified: [] };
  },
  async clearDocPermission(id) {
    docPerms.delete(id);
  },
  async shareChat(chatId, userIds) {
    const chat = chats.get(chatId);
    if (!chat) throw new Error(`chat not found: ${chatId}`);
    const have = new Set(chat.shared_with ?? []);
    for (const u of userIds) if (u !== (chat.owner ?? "default-user")) have.add(u);
    chat.shared_with = [...have];
  },
  async unshareChat(chatId, userId) {
    const chat = chats.get(chatId);
    if (!chat) throw new Error(`chat not found: ${chatId}`);
    chat.shared_with = (chat.shared_with ?? []).filter((u) => u !== userId);
  },
  async listWikiPages(collectionId) {
    return { pages: [...(wikiPages.get(collectionId)?.keys() ?? [])].sort() };
  },
  async getWikiPage(collectionId, path) {
    const content = wikiPages.get(collectionId)?.get(path);
    if (content == null) throw new Error(`no wiki page ${path}`);
    return { path, content };
  },
  async writeWikiPage(collectionId, path, content) {
    let pages = wikiPages.get(collectionId);
    if (!pages) {
      pages = new Map();
      wikiPages.set(collectionId, pages);
    }
    pages.set(path, content);
  },
  async moveWikiPage(collectionId, from, to) {
    const pages = wikiPages.get(collectionId);
    if (!pages || !pages.has(from)) throw new Error(`no wiki page ${from}`);
    pages.set(to, pages.get(from) as string);
    pages.delete(from);
  },
  async deleteWikiPage(collectionId, path) {
    wikiPages.get(collectionId)?.delete(path);
  },
  async rebuildWiki(collectionId) {
    // Synthesize a tiny wiki from the collection's docs so the browser has
    // something to show: an index linking to one entity page per document.
    const docs = documents.get(collectionId) ?? [];
    const pages = new Map<string, string>();
    pages.set("/WIKI.md", "# Wiki conventions\n");
    const links = docs.map((d) => `- [[${stem(d.path)}]]`).join("\n");
    pages.set("/index.md", `# Knowledge wiki\n\n${links}\n`);
    for (const d of docs) {
      pages.set(
        `/entities/${stem(d.path)}.md`,
        `# ${stem(d.path)}\n\nSynthesized from the source.\n\nSources: ${d.path}\n`,
      );
    }
    wikiPages.set(collectionId, pages);
    // The mock build completes instantly — report it done.
    wikiStatus.set(collectionId, {
      building: false,
      total: docs.length,
      done: docs.length,
      current: null,
      phase: null,
      errors: 0,
      last_error: null,
    });
    return { queued: docs.length, status: "rebuilding" };
  },
  async reflectWiki(collectionId) {
    // #479: the mock reflection completes instantly — stamp last_reflected_at so
    // the "Reflected …" strip updates, and report the build done.
    const c = collections.get(collectionId);
    if (c) collections.set(collectionId, { ...c, last_reflected_at: new Date().toISOString() });
    wikiStatus.set(collectionId, {
      building: false,
      total: 1,
      done: 1,
      current: null,
      phase: null,
      errors: 0,
      last_error: null,
    });
    return { queued: 1, status: "reflecting" };
  },
  async getWikiStatus(collectionId) {
    return (
      wikiStatus.get(collectionId) ?? {
        building: false,
        total: 0,
        done: 0,
        current: null,
        phase: null,
        errors: 0,
        last_error: null,
      }
    );
  },
  async syncCollection(collectionId) {
    // #355: simulate the async sync — stamp a fake synced commit so the dev
    // collection page shows the "Synced to …" strip.
    const c = collections.get(collectionId);
    const sha = "0".repeat(40);
    if (c) collections.set(collectionId, { ...c, git_last_sha: sha, git_last_pulled_at: Date.now() });
    return { status: "queued", git_last_sha: c?.git_last_sha ?? null };
  },
  async submitWikiCorrection(_collectionId, body) {
    // #397: pretend the correction landed on a per-target immune page.
    const slug = (body.target_page || "general").replace(/[^\w-]+/g, "-").replace(/^-+|-+$/g, "");
    return { path: `/corrections/${slug || "general"}.md` };
  },
  async draftWikiCorrection(_collectionId, body) {
    // #397 Q12: a deterministic stand-in — echo the flagged answer as a draft.
    return {
      action: "draft",
      instruction: `The answer "${body.answer}" is wrong; please correct it.`,
      target_page: body.wiki_pages?.[0] ?? "",
      questions: [],
    };
  },
  async listContextCards(collectionId) {
    return [...(contextCards.get(collectionId) ?? [])];
  },
  async createContextCard(input) {
    const list = contextCards.get(input.collection_id) ?? [];
    const id = nextId("card");
    list.push({
      id,
      collection_id: input.collection_id,
      keys: input.keys,
      norm_keys: deriveNormKeys(input.keys),
      title: input.title,
      body: input.body,
      reference_doc_ids: input.reference_doc_ids ?? [],
    });
    contextCards.set(input.collection_id, list);
    return id;
  },
  async updateContextCard(id, patch) {
    for (const [cid, list] of contextCards) {
      const i = list.findIndex((c) => c.id === id);
      if (i !== -1) {
        list[i] = { ...list[i], ...patch, norm_keys: deriveNormKeys(patch.keys) };
        contextCards.set(cid, list);
        return;
      }
    }
  },
  async deleteContextCard(id) {
    for (const [cid, list] of contextCards) {
      const next = list.filter((c) => c.id !== id);
      if (next.length !== list.length) contextCards.set(cid, next);
    }
  },

  async generateContextCards(collectionId, docIds) {
    // One proposal per selected doc, keyed by the doc's path stem; an existing
    // card with that normalised key makes it an `update`, mirroring the BE.
    const docs = documents.get(collectionId) ?? [];
    const existing = contextCards.get(collectionId) ?? [];
    const proposals: KbProposedCard[] = docIds.map((docId, i) => {
      const path = docs.find((d) => d.resource_id === docId)?.path ?? docId;
      const key = stem(path);
      const hit = existing.find((c) => c.norm_keys.includes(normKey(key)));
      return {
        id: String(i),
        keys: [key],
        title: key,
        body: `Auto-drafted from ${path}.`,
        confident: true,
        mode: hit ? "update" : "new",
        target_card_id: hit?.id ?? null,
        provenance: [{ doc_id: docId, path, snippet: `…relevant passage from ${path}…` }],
        decision: "pending",
      };
    });
    const jobId = nextId("cardgen");
    cardGenJobs.set(jobId, { collectionId, status: "completed", proposals });
    return jobId;
  },
  async getCardGenStatus(jobId) {
    const j = cardGenJobs.get(jobId);
    return { status: j?.status ?? "completed", proposals: j ? [...j.proposals] : [] };
  },
  async reviewCardGen(jobId, proposals) {
    const j = cardGenJobs.get(jobId);
    if (j) j.proposals = proposals;
    return { status: j?.status ?? "completed", proposals };
  },
  async commitCardGen(jobId) {
    const j = cardGenJobs.get(jobId);
    let created = 0;
    let updated = 0;
    let skipped = 0;
    for (const p of j?.proposals ?? []) {
      if (p.decision !== "accepted" || deriveNormKeys(p.keys).length === 0) {
        skipped++;
        continue;
      }
      if (p.mode === "update" && p.target_card_id) {
        await this.updateContextCard(p.target_card_id, {
          keys: p.keys,
          title: p.title,
          body: p.body,
        });
        updated++;
      } else {
        await this.createContextCard({
          collection_id: j?.collectionId ?? "",
          keys: p.keys,
          title: p.title,
          body: p.body,
        });
        created++;
      }
    }
    if (j) j.resolved = true; // reviewed → leaves the 待審核 queue (#415)
    return { created, updated, skipped };
  },
  async listCardGenRuns(collectionId) {
    return [...cardGenJobs.entries()]
      .filter(([, j]) => j.collectionId === collectionId && j.status === "completed" && !j.resolved)
      .map(([run_id, j]) => ({
        run_id,
        collection_id: collectionId,
        proposal_count: j.proposals.length,
      }));
  },
  async dismissCardGen(jobId) {
    const j = cardGenJobs.get(jobId);
    if (j) j.resolved = true;
  },

  async getReviewInbox(opts) {
    // #481: mirror the BE — a proposal is ACTIVE while pending/accepted; the
    // default view keeps active items, `resolved` keeps the terminal ones. The
    // mock has no permission model, so everything is actionable.
    const resolved = opts?.resolved ?? false;
    const scope = opts?.collectionId;
    const isActive = (d: string) => d === "pending" || d === "accepted";
    const cards: KbReviewCard[] = [];
    for (const [run_id, j] of cardGenJobs) {
      if (j.status !== "completed") continue;
      if (scope && j.collectionId !== scope) continue;
      const name = collections.get(j.collectionId)?.name ?? j.collectionId;
      for (const card of j.proposals) {
        if (isActive(card.decision) === resolved) continue;
        cards.push({
          run_id,
          collection_id: j.collectionId,
          collection_name: name,
          can_act: true,
          created_time: 0,
          card,
        });
      }
    }
    const questions: KbReviewQuestion[] = [];
    for (const q of docQuestions.values()) {
      if (scope && q.collection_id !== scope) continue;
      if ((q.status === "open") === resolved) continue;
      const name = collections.get(q.collection_id)?.name ?? q.collection_id;
      questions.push({
        collection_id: q.collection_id,
        collection_name: name,
        can_act: true,
        created_time: 0,
        question: q,
      });
    }
    return { cards, questions };
  },
  async decideCard(runId, cardId, decision) {
    const j = cardGenJobs.get(runId);
    const c = j?.proposals.find((p) => p.id === cardId);
    if (c) c.decision = decision as KbProposedCard["decision"];
    if (j) j.resolved = j.proposals.every((p) => p.decision === "committed" || p.decision === "rejected");
    return { status: j?.status ?? "completed", proposals: j ? [...j.proposals] : [] };
  },
  async updateProposal(runId, card) {
    const j = cardGenJobs.get(runId);
    const idx = j?.proposals.findIndex((p) => p.id === card.id) ?? -1;
    if (j && idx >= 0) j.proposals[idx] = { ...card };
    if (j) j.resolved = j.proposals.every((p) => p.decision === "committed" || p.decision === "rejected");
    return { status: j?.status ?? "completed", proposals: j ? [...j.proposals] : [] };
  },
  async commitCards(cards) {
    let created = 0;
    let updated = 0;
    let skipped = 0;
    const byRun = new Map<string, Set<string>>();
    for (const { run_id, card_id } of cards) {
      (byRun.get(run_id) ?? byRun.set(run_id, new Set()).get(run_id)!).add(card_id);
    }
    for (const [runId, ids] of byRun) {
      const j = cardGenJobs.get(runId);
      if (!j) continue;
      for (const p of j.proposals) {
        if (!ids.has(p.id)) continue;
        const active = p.decision === "pending" || p.decision === "accepted";
        if (!active || deriveNormKeys(p.keys).length === 0) {
          skipped++;
          continue;
        }
        if (p.mode === "update" && p.target_card_id) {
          await this.updateContextCard(p.target_card_id, {
            keys: p.keys,
            title: p.title,
            body: p.body,
          });
          updated++;
        } else {
          await this.createContextCard({
            collection_id: j.collectionId,
            keys: p.keys,
            title: p.title,
            body: p.body,
          });
          created++;
        }
        p.decision = "committed";
      }
      j.resolved = j.proposals.every((p) => p.decision === "committed" || p.decision === "rejected");
    }
    return { created, updated, skipped };
  },

  async getDocQuestions(collectionId) {
    return [...docQuestions.values()].filter(
      (q) => q.status === "open" && (!collectionId || q.collection_id === collectionId),
    );
  },
  async answerDocQuestion(id, _answer) {
    const q = docQuestions.get(id);
    if (q) docQuestions.set(id, { ...q, status: "answered" });
    return q?.kind === "description" ? "/clarifications.md" : nextId("card");
  },
  async discardDocQuestion(id) {
    const q = docQuestions.get(id);
    if (q) docQuestions.set(id, { ...q, status: "discarded" });
  },

  async cancelMessage(_chatId) {
    // No server turn to cancel in the mock; the FE aborts the stream locally.
  },
  async *streamMessage(args: SendKbMessageArgs): AsyncGenerator<AgentEvent> {
    const chat = chats.get(args.chatId);
    if (!chat) throw new Error(`chat not found: ${args.chatId}`);
    chat.messages.push(blankUser(args.content));
    touchChat(chat.resource_id); // #357: a new turn bumps recency

    const answer = "Per the knowledge base, reflow zone three drifted [1].";
    await delay(40);
    yield { type: "tool_start", call_id: "t1", name: "kb_search", args: { query: args.content } };
    yield { type: "tool_end", call_id: "t1", output: "[1] reflow.md: zone three drift" };
    yield { type: "message_delta", text: answer, reasoning: false };
    await delay(40);

    chat.messages.push({
      role: "tool",
      content: "[1] reflow.md: zone three drift",
      reasoning: null,
      tool_name: "kb_search",
      tool_args: { query: args.content },
      tool_call_id: "t1",
      created_at: Date.now(),
      citations: [],
    });
    chat.messages.push({
      role: "assistant",
      content: answer,
      reasoning: null,
      tool_name: null,
      tool_args: null,
      tool_call_id: null,
      created_at: Date.now(),
      citations: [
        {
          marker: 1,
          collection_id: chat.collection_ids[0] ?? "col-1",
          document_id: `${chat.collection_ids[0] ?? "col-1"}/me/reflow.md`,
          filename: "reflow.md",
          start: 0,
          end: 16,
          source_chunk_ids: ["reflow.md#0"],
          snippet: "zone three drift",
        },
      ],
    });
    yield { type: "done" };
  },
};

function blankUser(content: string): KbChatMessage {
  return {
    role: "user",
    content,
    reasoning: null,
    tool_name: null,
    tool_args: null,
    tool_call_id: null,
    created_at: Date.now(),
    citations: [],
  };
}

/** Internal — seed a collection's wiki pages for tests. */
export const _seedWikiMock = (collectionId: string, pages: Record<string, string>) => {
  wikiPages.set(collectionId, new Map(Object.entries(pages)));
};

/** Internal — seed an open clarification question (#377) for tests. */
export const _seedDocQuestionMock = (q: Partial<KbDocQuestion> & { id: string }) => {
  docQuestions.set(q.id, {
    collection_id: "col-1",
    kind: "term",
    status: "open",
    question_text: "",
    term: "",
    source_doc_ids: [],
    source_doc_id: "",
    quote: "",
    ...q,
  });
};

/** Internal — seed a collection's live build status for tests. */
export const _setWikiStatusMock = (
  collectionId: string,
  status: {
    building: boolean;
    total: number;
    done: number;
    current?: string | null;
    phase?: string | null;
    errors?: number;
    last_error?: string | null;
  },
) => {
  wikiStatus.set(collectionId, {
    current: null,
    phase: null,
    errors: 0,
    last_error: null,
    ...status,
  });
};

/** Internal — reset between tests. */
export const _resetKbMock = () => {
  collections.clear();
  documents.clear();
  docChunks.clear();
  chats.clear();
  wikiPages.clear();
  wikiStatus.clear();
  contextCards.clear();
  cardGenJobs.clear();
  seq = 0;
};
