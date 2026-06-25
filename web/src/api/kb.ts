/**
 * KB (knowledge-base chatbot) API client — collections, documents, chat
 * threads, and the streaming chat turn. Separate from the investigation
 * `ApiClient`: the KB is its own subsystem. Mock/real swap on the same
 * `VITE_USE_MOCK` switch as `./index`.
 *
 * Wire shapes mirror `api/kb_routes.py` + `api/kb_chat_routes.py`.
 */

import type { AgentEvent } from "../events";
import { API_PREFIX, apiFetch } from "./http";
import { mockKbApi } from "./kbMock";
import { parseSseStream } from "./sse";
import type { ReasoningEffort } from "./types";

export type KbCollection = {
  resource_id: string;
  name: string;
  description: string;
  /** Icon name (Icon set) for the collection card. */
  icon: string;
  /** How many times this collection's docs have been cited (P2 analytics). */
  cited: number;
  /** Card aggregates derived from the collection's documents. */
  doc_count: number;
  size: number; // total bytes
  updated_at: number; // epoch ms
  owner: string; // created_by
  /** Issue #50: which retrieval pipeline(s) this collection uses. `use_rag`
   * (default on) = chunk-RAG; `use_wiki` = the parallel LLM wiki the
   * maintainer builds on ingest + the reader navigates at query. */
  use_rag: boolean;
  use_wiki: boolean;
  /** Issue #90: per-collection wiki guidance, appended onto the bundled wiki
   * prompts. `maintainer` shapes how pages are written; `reader` shapes how the
   * wiki answers. Blank ⇒ the bundled prompt is used as-is. */
  wiki_maintainer_guidance: string;
  wiki_reader_guidance: string;
};

/** Issue #101: result of preparing a collection export — the handle to stream
 * the zip via `streamCollectionDownloadUrl`. `size` is the built zip's bytes. */
export type DownloadPrepared = {
  download_id: string;
  filename: string;
  size: number;
};

/** Issue #101: result of importing an exported zip. `collection_id` is the
 * target (a new collection, or the existing one merged into); docs land as
 * `status="indexing"` and re-index off-request. */
export type CollectionImported = {
  collection_id: string;
  document_ids: string[];
  status: string;
};

/** The LLM wiki's page paths for a collection (the read-only browser's tree). */
export type WikiTree = { pages: string[] };

/** One wiki page's raw markdown (rendered read-only client-side). */
export type WikiPage = { path: string; content: string };

/** Result of triggering a wiki rebuild — how many sources were queued. */
export type WikiRebuild = { queued: number; status: string };

/** Live wiki-build progress (the "Updating…" UI polls this). `phase` is the
 * current activity: "reading" | "identifying" | "writing" | null. */
export type WikiStatus = {
  building: boolean;
  total: number;
  done: number;
  current: string | null;
  phase: string | null;
  /** Terminal failures this build (e.g. the maintainer hit its step limit) so
   * a wiki that built nothing explains why instead of looking merely empty. */
  errors: number;
  last_error: string | null;
};

/** Create-time options for a collection's retrieval pipeline toggles. */
export type CollectionOptions = { useRag?: boolean; useWiki?: boolean };

export type KbDocument = {
  resource_id: string;
  path: string;
  content_type: string;
  /** The content blob id — lets the doc IDE resolve a sibling image ref to
   * `/source-doc/{resource_id}/blobs/{file_id}` (#87). Optional on the wire like
   * the other derived fields; the real BE always sends it. */
  file_id?: string;
  created_by: string;
  /** Indexing lifecycle: "indexing" | "ready" | "error". */
  status: string;
  /** Issue #39 Q11: short progress / error line beside the status —
   * "PdfParser: page 12/50 → VLM" while a long parse runs, the
   * exception summary on `status="error"`. Empty when idle. */
  status_detail?: string;
  /** Indexed chunk count + how many times this doc was cited. */
  chunks?: number;
  cited?: number;
  /** Stored blob size in bytes + the resource's last-update time (epoch ms). */
  size?: number;
  updated_at?: number;
};

/** One page of documents inside a collection. The BE pages through specstar's
 * `QB[...].offset(offset).limit(limit)` so even a many-thousand-document
 * collection doesn't materialise the whole list to slice it. `total` is the
 * full collection size (filtered by collection_id) — drives `n of N` UI; the
 * `has_more` convenience saves the caller from re-deriving it. */
export type KbDocumentsPage = {
  items: KbDocument[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
};

export type KbDocChunk = {
  chunk_id: string;
  seq: number;
  start: number;
  end: number;
  text: string;
  cited: number;
};

/** A document rendered for the viewer drawer: markdown (kb:// links) plus the
 * metadata its header + actions need. `file_id` → download via GET /blobs/{id}. */
export type KbRenderedDoc = {
  document_id: string;
  filename: string;
  collection_id: string;
  markdown: string;
  file_id: string;
  content_type: string;
  size: number;
  chunks: number;
  cited: number;
  created_by: string;
  updated_at: number; // epoch ms
  status: string;
  /** Issue #39 Q11 — progress / error line beside the status chip. */
  status_detail?: string;
  /** Issue #39: blob id of a browser-displayable derivative a parser
   * handed back (pptx → soffice-converted PDF). The viewer iframes
   * `/blobs/{preview_file_id}` when set; "" / absent = no preview. */
  preview_file_id?: string;
};

/** A resolved [n] marker — points at a span of a source document. */
export type KbCitation = {
  marker: number;
  collection_id: string;
  document_id: string;
  filename: string;
  start: number;
  end: number;
  source_chunk_ids: string[];
  snippet: string;
};

export type KbChatMessage = {
  role: "user" | "assistant" | "tool" | "error";
  content: string;
  reasoning: string | null;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_call_id: string | null;
  created_at: number | null;
  citations: KbCitation[];
  /** role=error only (#37) — error | cancelled | max_turns. */
  error_kind?: string | null;
  /** #113 — "repetition" when the answer was stopped + truncated for a loop. */
  stopped_reason?: string | null;
};

export type KbChatSummary = {
  resource_id: string;
  title: string;
  collection_ids: string[];
  message_count: number;
  owner?: string;
  shared_with?: string[];
};

export type KbChatDetail = {
  resource_id: string;
  title: string;
  collection_ids: string[];
  messages: KbChatMessage[];
  owner?: string;
  shared_with?: string[];
};

export type SendKbMessageArgs = {
  chatId: string;
  content: string;
  signal?: AbortSignal;
  reasoningEffort?: ReasoningEffort;
  /** Per-message enhancement override (Phase C — Hybrid picker).
   * Concrete numbers / bool override the operator default for that
   * knob; the BE clamps each to the operator's `max` before running.
   * `undefined` (or no payload) inherits the operator default — the
   * "Standard" Mode preset on the FE picker. */
  enhancements?: {
    expand?: number | null;
    hyde?: number | null;
    rerank?: boolean | null;
    /** Issue #50 P6: opt this query into the wiki path (the depth picker's
     * "Search the wiki" toggle). The BE routes chunk / wiki / both. */
    wiki?: boolean | null;
  };
  /** Issue #32: which kb_chat entry to drive this turn (the picker
   * value). undefined → the first kb_chats[] entry. */
  agentName?: string;
};

export type KbAgentConfig = {
  /** Picker label — uniquely identifies this kb_chats[] entry. */
  name: string;
  /** The LLM model string (e.g. `openai/gpt-4o-mini`) — shown in the
   * picker row so the operator can tell GPT vs Claude vs local. */
  model: string;
  /** One-line picker blurb rendered under the entry name. */
  description?: string;
  /** Quick-prompt chips for the chat empty state. Each entry has a ``label``
   * (button text) and a ``prompt`` (sent verbatim on click). See #91. */
  suggestions: import("./types").Suggestion[];
};

/** #106: a context card — several `keys` (a term + its surface forms) → a short
 * markdown `body`, looked up deterministically by exact key. `id` is the
 * specstar resource id; `norm_keys` is the server-derived lookup surface (the FE
 * never sends it). */
export type KbContextCard = {
  id: string;
  collection_id: string;
  keys: string[];
  norm_keys: string[];
  title: string;
  body: string;
};

/** Author input for create/update — no `norm_keys` (server-derived). */
export type KbContextCardInput = {
  collection_id: string;
  keys: string[];
  title: string;
  body: string;
};

export interface KbApi {
  /** The KB agent picker (issue #32): an ARRAY of {name, model,
   * suggestions}. FE renders a dropdown; first entry is the default. */
  getAgentConfig(): Promise<KbAgentConfig[]>;
  listCollections(): Promise<KbCollection[]>;
  createCollection(
    name: string,
    description?: string,
    opts?: CollectionOptions,
  ): Promise<KbCollection>;
  /** Rename / change icon / edit description / retrieval toggles — via
   * specstar's native PATCH /collection/{id} (partial update). */
  updateCollection(
    id: string,
    patch: {
      name?: string;
      icon?: string;
      description?: string;
      use_rag?: boolean;
      use_wiki?: boolean;
      wiki_maintainer_guidance?: string;
      wiki_reader_guidance?: string;
    },
  ): Promise<void>;
  /** Permanently delete — specstar's native DELETE /collection/{id}/permanently. */
  deleteCollection(id: string): Promise<void>;
  /** Re-chunk + re-embed documents in the collection (recovers `error` docs
   * after an embedder fix). Each re-queued doc flips back to `indexing`.
   * `{ only: "failed" }` re-queues ONLY docs stuck in `error` (issue #223);
   * omitted re-indexes the whole collection. */
  reindexCollection(id: string, opts?: { only?: "failed" }): Promise<void>;
  listDocuments(
    collectionId: string,
    page?: { offset?: number; limit?: number },
  ): Promise<KbDocumentsPage>;
  /** Multipart upload; returns the ingested document ids (one per archive
   * member). `path` overrides the stored filename — used for folder uploads to
   * preserve each file's relative path. */
  uploadDocument(collectionId: string, file: File, path?: string): Promise<string[]>;
  /** Issue #101: build the collection's export zip and return its handle. The
   * zip is held server-side until streamed (or reaped); two-step so a large
   * export's build latency hides behind a loading state, not a stalled click. */
  prepareCollectionDownload(collectionId: string): Promise<DownloadPrepared>;
  /** The URL to navigate to (native streaming download) for a prepared export.
   * Not a fetch — an `<a href>` so the browser streams straight to disk. */
  streamCollectionDownloadUrl(collectionId: string, downloadId: string): string;
  /** Issue #101: import an exported zip as a NEW collection (settings + cards
   * restored from its manifest; a manifest-less zip becomes a plain-files
   * import named after the file). Returns the new collection id. */
  importCollectionNew(file: File): Promise<CollectionImported>;
  /** Issue #101: merge an exported zip INTO an existing collection. `mode`
   * resolves a path collision: `overwrite` (last-write-wins) or `skip`. */
  importCollectionInto(
    collectionId: string,
    file: File,
    mode: "overwrite" | "skip",
  ): Promise<CollectionImported>;
  /** Render a source document to markdown (kb:// links) for the citation viewer. */
  renderDocument(documentId: string): Promise<KbRenderedDoc>;
  /** A document's indexed chunks + their cited counts (the chunks debug view). */
  getDocChunks(documentId: string): Promise<KbDocChunk[]>;
  /** Re-chunk + re-embed a single document (flips it back to `indexing`). */
  reindexDocument(documentId: string): Promise<void>;
  /** Remove a document and its chunks (cascade) — DELETE /kb/documents?id=. */
  deleteDocument(documentId: string): Promise<void>;
  /** Rename / move a document to a new path. Re-keys it (the id encodes the
   * path) preserving the creator, then re-indexes — POST /kb/documents/move. */
  moveDocument(documentId: string, to: string): Promise<void>;

  /** The LLM wiki's page paths for a collection (read-only browser tree). */
  listWikiPages(collectionId: string): Promise<WikiTree>;
  /** One wiki page's markdown by path. */
  getWikiPage(collectionId: string, path: string): Promise<WikiPage>;
  /** Write (create or overwrite) a wiki page's markdown (#D editable wiki). */
  writeWikiPage(collectionId: string, path: string, content: string): Promise<void>;
  /** Move / rename a wiki page. */
  moveWikiPage(collectionId: string, from: string, to: string): Promise<void>;
  /** Delete a wiki page. */
  deleteWikiPage(collectionId: string, path: string): Promise<void>;
  /** Re-fold every source into the wiki (the manual "rebuild" button). */
  rebuildWiki(collectionId: string): Promise<WikiRebuild>;
  /** Live build progress, polled while a wiki is being (re)built. */
  getWikiStatus(collectionId: string): Promise<WikiStatus>;

  /** #106: a collection's context cards (the lightweight glossary). Lists via
   * specstar's auto CRUD route, scoped on the indexed `collection_id`. */
  listContextCards(collectionId: string): Promise<KbContextCard[]>;
  /** Author a new card — POST the create custom action; the server derives
   * `norm_keys` from `keys` in the same write. Returns the new card's id so the
   * editor switches from "new" to "editing" (a second save updates, not dupes). */
  createContextCard(input: KbContextCardInput): Promise<string>;
  /** Edit a card's keys/title/body — POST the update custom action (collection
   * stays put; `norm_keys` re-derived server-side). */
  updateContextCard(id: string, patch: Omit<KbContextCardInput, "collection_id">): Promise<void>;
  /** Permanently remove a card — specstar's native hard delete. */
  deleteContextCard(id: string): Promise<void>;

  listChats(): Promise<KbChatSummary[]>;
  createChat(title: string, collectionIds: string[]): Promise<KbChatSummary>;
  getChat(chatId: string): Promise<KbChatDetail>;
  deleteChat(chatId: string): Promise<void>;
  /** Owner-only: share a thread read-only with users (they get a notification). */
  shareChat(chatId: string, userIds: string[]): Promise<void>;
  unshareChat(chatId: string, userId: string): Promise<void>;
  /** Stream one chat turn. Citations are not in the stream — refetch the chat
   * on done to get the persisted assistant message with its [n] resolved. */
  streamMessage(args: SendKbMessageArgs): AsyncGenerator<AgentEvent>;
  /** Interrupt the chat's in-flight turn server-side (the stream gets a
   * run_cancelled event, then closes). Mirrors the RCA workspace cancel. */
  cancelMessage(chatId: string): Promise<void>;
}

/* ------------------------------- real ------------------------------- */

async function ok(resp: Response, what: string): Promise<Response> {
  if (!resp.ok) throw new Error(`${what} failed: ${resp.status}`);
  return resp;
}

const jsonHeaders = { "content-type": "application/json" };

export const realKbApi: KbApi = {
  async getAgentConfig() {
    return (await ok(await apiFetch("/kb/agent"), "kb agent config")).json();
  },
  async listCollections() {
    return (await ok(await apiFetch("/kb/collections"), "list collections")).json();
  },
  async createCollection(name, description = "", opts) {
    const resp = await ok(
      await apiFetch("/kb/collections", {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({
          name,
          description,
          // Omit when unset so the BE defaults (use_rag on, use_wiki off) apply.
          ...(opts?.useRag != null ? { use_rag: opts.useRag } : {}),
          ...(opts?.useWiki != null ? { use_wiki: opts.useWiki } : {}),
        }),
      }),
      "create collection",
    );
    return resp.json();
  },
  async updateCollection(id, patch) {
    // specstar's native resource CRUD — PATCH a partial onto the Collection.
    await ok(
      await apiFetch(`/collection/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: jsonHeaders,
        body: JSON.stringify(patch),
      }),
      "update collection",
    );
  },
  async deleteCollection(id) {
    // native hard delete (soft delete would still show under list's QB.all()).
    await ok(
      await apiFetch(`/collection/${encodeURIComponent(id)}/permanently`, { method: "DELETE" }),
      "delete collection",
    );
  },
  async reindexCollection(id, opts) {
    const qs = opts?.only ? `?only=${encodeURIComponent(opts.only)}` : "";
    await ok(
      await apiFetch(`/kb/collections/${encodeURIComponent(id)}/reindex${qs}`, { method: "POST" }),
      "reindex collection",
    );
  },
  async listDocuments(collectionId, page) {
    const qs = new URLSearchParams();
    if (page?.offset != null) qs.set("offset", String(page.offset));
    if (page?.limit != null) qs.set("limit", String(page.limit));
    const path = `/kb/collections/${encodeURIComponent(collectionId)}/documents`;
    const url = qs.size ? `${path}?${qs.toString()}` : path;
    const resp = await apiFetch(url);
    return (await ok(resp, "list documents")).json();
  },
  async uploadDocument(collectionId, file, path) {
    const form = new FormData();
    form.append("file", file, path ?? file.name);
    const resp = await ok(
      await apiFetch(`/kb/collections/${encodeURIComponent(collectionId)}/documents`, {
        method: "POST",
        body: form,
      }),
      "upload document",
    );
    return (await resp.json()).document_ids;
  },
  async prepareCollectionDownload(collectionId) {
    const resp = await ok(
      await apiFetch(
        `/kb/collections/${encodeURIComponent(collectionId)}/download/prepare`,
        { method: "POST" },
      ),
      "prepare collection download",
    );
    return resp.json();
  },
  streamCollectionDownloadUrl(collectionId, downloadId) {
    return `${API_PREFIX}/kb/collections/${encodeURIComponent(collectionId)}/download/${encodeURIComponent(downloadId)}`;
  },
  async importCollectionNew(file) {
    const form = new FormData();
    form.append("file", file, file.name);
    const resp = await ok(
      await apiFetch("/kb/collections/import", { method: "POST", body: form }),
      "import collection",
    );
    return resp.json();
  },
  async importCollectionInto(collectionId, file, mode) {
    const form = new FormData();
    form.append("file", file, file.name);
    const resp = await ok(
      await apiFetch(
        `/kb/collections/${encodeURIComponent(collectionId)}/import?mode=${mode}`,
        { method: "POST", body: form },
      ),
      "import into collection",
    );
    return resp.json();
  },
  async renderDocument(documentId) {
    // documentId is an opaque, slash-free token — pass it as a query param so
    // it round-trips a URL untouched.
    const url = `/kb/documents?id=${encodeURIComponent(documentId)}`;
    return (await ok(await apiFetch(url), "render document")).json();
  },
  async getDocChunks(documentId) {
    const url = `/kb/documents/chunks?id=${encodeURIComponent(documentId)}`;
    return (await ok(await apiFetch(url), "list doc chunks")).json();
  },
  async reindexDocument(documentId) {
    await ok(
      await apiFetch(`/kb/documents/reindex?id=${encodeURIComponent(documentId)}`, {
        method: "POST",
      }),
      "reindex document",
    );
  },
  async deleteDocument(documentId) {
    await ok(
      await apiFetch(`/kb/documents?id=${encodeURIComponent(documentId)}`, { method: "DELETE" }),
      "delete document",
    );
  },
  async moveDocument(documentId, to) {
    const qs = `id=${encodeURIComponent(documentId)}&to=${encodeURIComponent(to)}`;
    await ok(
      await apiFetch(`/kb/documents/move?${qs}`, { method: "POST" }),
      "move document",
    );
  },

  async listWikiPages(collectionId) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/wiki`;
    return (await ok(await apiFetch(url), "list wiki pages")).json();
  },
  async getWikiPage(collectionId, path) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/wiki/page?path=${encodeURIComponent(path)}`;
    return (await ok(await apiFetch(url), "get wiki page")).json();
  },
  async writeWikiPage(collectionId, path, content) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/wiki/page?path=${encodeURIComponent(path)}`;
    await ok(
      await apiFetch(url, {
        method: "PUT",
        headers: { "content-type": "text/markdown" },
        body: content,
      }),
      "write wiki page",
    );
  },
  async moveWikiPage(collectionId, from, to) {
    const qs = `from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`;
    await ok(
      await apiFetch(`/kb/collections/${encodeURIComponent(collectionId)}/wiki/move?${qs}`, {
        method: "POST",
      }),
      "move wiki page",
    );
  },
  async deleteWikiPage(collectionId, path) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/wiki/page?path=${encodeURIComponent(path)}`;
    await ok(await apiFetch(url, { method: "DELETE" }), "delete wiki page");
  },
  async rebuildWiki(collectionId) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/wiki/rebuild`;
    return (await ok(await apiFetch(url, { method: "POST" }), "rebuild wiki")).json();
  },
  async getWikiStatus(collectionId) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/wiki/status`;
    return (await ok(await apiFetch(url), "wiki status")).json();
  },

  async listContextCards(collectionId) {
    // specstar auto CRUD list, filtered on the indexed collection_id; the
    // response is the specstar envelope, so flatten data + the resource id.
    const qb = `QB['collection_id'] == '${collectionId}'`;
    const url = `/context-card?qb=${encodeURIComponent(qb)}`;
    const rows: { data: Omit<KbContextCard, "id">; revision_info: { resource_id: string } }[] =
      await (await ok(await apiFetch(url), "list context cards")).json();
    return rows.map((r) => ({ id: r.revision_info.resource_id, ...r.data }));
  },
  async createContextCard(input) {
    const resp = await ok(
      await apiFetch("/context-card/author", {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify(input),
      }),
      "create context card",
    );
    // the create action responds with specstar's RevisionInfo — return the id.
    const info: { resource_id: string } = await resp.json();
    return info.resource_id;
  },
  async updateContextCard(id, patch) {
    await ok(
      await apiFetch(`/context-card/${encodeURIComponent(id)}/edit`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify(patch),
      }),
      "update context card",
    );
  },
  async deleteContextCard(id) {
    await ok(
      await apiFetch(`/context-card/${encodeURIComponent(id)}/permanently`, { method: "DELETE" }),
      "delete context card",
    );
  },

  async listChats() {
    return (await ok(await apiFetch("/kb/chats"), "list chats")).json();
  },
  async createChat(title, collectionIds) {
    const resp = await ok(
      await apiFetch("/kb/chats", {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ title, collection_ids: collectionIds }),
      }),
      "create chat",
    );
    const data = await resp.json();
    return { ...data, message_count: 0 };
  },
  async getChat(chatId) {
    return (await ok(await apiFetch(`/kb/chats/${encodeURIComponent(chatId)}`), "get chat")).json();
  },
  async deleteChat(chatId) {
    await ok(
      await apiFetch(`/kb/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" }),
      "delete chat",
    );
  },
  async shareChat(chatId, userIds) {
    await ok(
      await apiFetch(`/kb/chats/${encodeURIComponent(chatId)}/share`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ user_ids: userIds }),
      }),
      "share chat",
    );
  },
  async unshareChat(chatId, userId) {
    await ok(
      await apiFetch(
        `/kb/chats/${encodeURIComponent(chatId)}/share/${encodeURIComponent(userId)}`,
        { method: "DELETE" },
      ),
      "unshare chat",
    );
  },
  async *streamMessage(args) {
    const resp = await apiFetch(`/kb/chats/${encodeURIComponent(args.chatId)}/messages`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        content: args.content,
        reasoning_effort: args.reasoningEffort,
        enhancements: args.enhancements,
        agent_name: args.agentName,
      }),
      signal: args.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`kb message failed: ${resp.status}`);
    yield* parseSseStream(resp.body);
  },
  async cancelMessage(chatId) {
    await ok(
      await apiFetch(`/kb/chats/${encodeURIComponent(chatId)}/messages/current`, {
        method: "DELETE",
      }),
      "cancel kb message",
    );
  },
};

/* ----------------------------- selector ----------------------------- */

const useMock = import.meta.env.VITE_USE_MOCK === "1";

export const kbApi: KbApi = useMock ? mockKbApi : realKbApi;
