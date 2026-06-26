/**
 * In-memory KB API for dev/tests (VITE_USE_MOCK=1). Mirrors the real client's
 * observable behavior: create/list collections, upload→list documents, chat
 * thread CRUD, and a scripted streaming turn whose persisted answer carries a
 * citation (so the UI's citation path is exercisable without a backend).
 */

import type { AgentEvent } from "../events";
import type {
  KbApi,
  KbCardGenStatus,
  KbChatDetail,
  KbChatMessage,
  KbChatSummary,
  KbCollection,
  KbContextCard,
  KbDocChunk,
  KbDocument,
  KbProposedCard,
  KbRenderedDoc,
  SendKbMessageArgs,
} from "./kb";

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${(++seq).toString(36)}`;

const collections = new Map<string, KbCollection>();
const documents = new Map<string, KbDocument[]>();
const docChunks = new Map<string, KbDocChunk[]>();
const chats = new Map<string, KbChatDetail>();
// collectionId → its context cards (#106), keyed by collection like documents.
const contextCards = new Map<string, KbContextCard[]>();
// jobId → a 自動 context card generation run (#175). The mock completes the run
// synchronously (status "completed") with one proposal per selected document.
const cardGenJobs = new Map<
  string,
  { collectionId: string; status: KbCardGenStatus["status"]; proposals: KbProposedCard[] }
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
  return {
    resource_id: chat.resource_id,
    title: chat.title,
    collection_ids: chat.collection_ids,
    message_count: chat.messages.length,
    owner: chat.owner ?? "default-user",
    shared_with: chat.shared_with ?? [],
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
      updated_at: Date.now(),
      owner: "me",
      use_rag: opts?.useRag ?? true,
      use_wiki: opts?.useWiki ?? false,
      wiki_maintainer_guidance: "",
      wiki_reader_guidance: "",
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
  },
  async listDocuments(collectionId, page) {
    const all = documents.get(collectionId) ?? [];
    // Mirror the BE sort (most-recent first) so paging looks the same as
    // production. Use updated_at when present; fall back to insertion order.
    const sorted = [...all].sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
    const offset = page?.offset ?? 0;
    const limit = page?.limit ?? 50;
    const items = sorted.slice(offset, offset + limit);
    return {
      items,
      total: sorted.length,
      offset,
      limit,
      has_more: offset + items.length < sorted.length,
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
      updated_at: Date.now(),
      owner: "me",
      use_rag: true,
      use_wiki: false,
      wiki_maintainer_guidance: "",
      wiki_reader_guidance: "",
    };
    collections.set(c.resource_id, c);
    return { collection_id: c.resource_id, document_ids: [], status: "indexing" };
  },
  async importCollectionInto(collectionId, _file, _mode) {
    return { collection_id: collectionId, document_ids: [], status: "indexing" };
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
  async createChat(title, collectionIds) {
    const chat: KbChatDetail = {
      resource_id: nextId("chat"),
      title: title || "New chat",
      collection_ids: collectionIds,
      messages: [],
    };
    chats.set(chat.resource_id, chat);
    return summarize(chat);
  },
  async getChat(chatId) {
    const chat = chats.get(chatId);
    if (!chat) throw new Error(`chat not found: ${chatId}`);
    return structuredClone(chat);
  },
  async deleteChat(chatId) {
    chats.delete(chatId);
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
    const proposals: KbProposedCard[] = docIds.map((docId) => {
      const path = docs.find((d) => d.resource_id === docId)?.path ?? docId;
      const key = stem(path);
      const hit = existing.find((c) => c.norm_keys.includes(normKey(key)));
      return {
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
    return { created, updated, skipped };
  },

  async cancelMessage(_chatId) {
    // No server turn to cancel in the mock; the FE aborts the stream locally.
  },
  async *streamMessage(args: SendKbMessageArgs): AsyncGenerator<AgentEvent> {
    const chat = chats.get(args.chatId);
    if (!chat) throw new Error(`chat not found: ${args.chatId}`);
    chat.messages.push(blankUser(args.content));

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
