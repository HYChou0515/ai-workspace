/**
 * KB (knowledge-base chatbot) API client — collections, documents, chat
 * threads, and the streaming chat turn. Separate from the investigation
 * `ApiClient`: the KB is its own subsystem. Mock/real swap on the same
 * `VITE_USE_MOCK` switch as `./index`.
 *
 * Wire shapes mirror `api/kb_routes.py` + `api/kb_chat_routes.py`.
 */

import type { AgentEvent } from "../events";
import type { UploadCheckHint } from "../kb/uploadChecks";
import { API_PREFIX, apiFetch } from "./http";
import { mockKbApi } from "./kbMock";
import { parseSseStream } from "./sse";
import type { Provenance, ReasoningEffort } from "./types";

export type { UploadCheckHint } from "../kb/uploadChecks";

/**
 * Thrown by `uploadDocument` when an upload check refuses the file (#325):
 * the server returns a 422 with `{check_id, reason_code, message_key}`. The
 * UI catches it to show the localised `messageKey` ("decrypt and re-upload")
 * in the "can't accept" list rather than treating it as a generic failure.
 */
export class UploadBlockedError extends Error {
  constructor(
    readonly messageKey: string,
    readonly checkId?: string,
    readonly reasonCode?: string,
  ) {
    super(`upload blocked: ${checkId ?? "unknown"}`);
    this.name = "UploadBlockedError";
  }
}

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
  /** #88: chunk-based token estimate — SUM of each ready doc's CJK-aware
   * token_count of the EXTRACTED text. The grid's "≈ N tokens" sums this,
   * instead of the old raw-blob `bytes / 4` guess. */
  tokens: number;
  updated_at: number; // epoch ms
  owner: string; // created_by
  /** Issue #50: which retrieval pipeline(s) this collection uses. `use_rag`
   * (default on) = chunk-RAG; `use_wiki` = the parallel LLM wiki the
   * maintainer builds on ingest + the reader navigates at query. */
  use_rag: boolean;
  use_wiki: boolean;
  /** P3.0 / #281: the git remote this collection syncs from, if any. A non-empty
   * `git_url` marks a CODE collection (its wiki is built from source by the
   * hierarchical builder, and refreshes only on sync / rebuild). Absent ⇒ a
   * plain document collection. */
  git_url?: string | null;
  /** #355: the branch synced (null ⇒ remote default). */
  git_branch?: string | null;
  /** #355: the commit sha of the last SUCCESSFUL sync, and the wall-clock ms of
   * the last sync attempt — drive the collection page's "Synced to … · …ago"
   * strip. Both null until the first sync. */
  git_last_sha?: string | null;
  git_last_pulled_at?: number | null;
  /** Issue #90: per-collection wiki guidance, appended onto the bundled wiki
   * prompts. `maintainer` shapes how pages are written; `reader` shapes how the
   * wiki answers. Blank ⇒ the bundled prompt is used as-is. */
  wiki_maintainer_guidance: string;
  wiki_reader_guidance: string;
  /** #105: the per-collection quality rubric — what makes a doc a good/bad
   * knowledge source + which dimensions to assess. Blank ⇒ the collection is
   * not scored and its search ranking is unaffected. Optional on the wire (the
   * real BE always sends it, defaulting to ""); absent ⇒ treat as "". */
  quality_rubric?: string;
  /** #328: the per-collection parser guidance — a free-text prompt appended to
   * every prompt-driven parser's base prompt (e.g. "a fishbone diagram → emit
   * JSON"). Blank ⇒ no steering. Optional on the wire (BE defaults to ""). */
  parser_guidance?: string;
};

/** #328 findability probe: where a doc's content ranks for a question, and how
 * a candidate parser_guidance would change it. */
export type KbProbePassage = {
  /** 1-based position in the deep ranked list (across the whole collection). */
  rank: number;
  /** Within the top_k a normal search returns — i.e. what a user actually sees. */
  in_top_k: boolean;
  /** A preview of the passage text (truncated). */
  text: string;
  /** Human structural locator ("p.3" / "slide 2 · Ch.2"), or "" when none. */
  location: string;
};

export type KbProbeSide = {
  /** The target doc's passages within the search depth, best rank first. */
  passages: KbProbePassage[];
  /** The doc's best (lowest) rank, or null if it didn't surface at all. */
  best_rank: number | null;
};

export type KbProbeResult = {
  /** The user-facing result cut — ranks ≤ top_k are "what the user sees". */
  top_k: number;
  /** How deep the probe ranked (the cap on `rank`). */
  depth: number;
  /** Where the doc's currently-indexed chunks rank for the question. */
  before: KbProbeSide;
  /** The candidate-guidance re-parse ranks — null when no guidance was given. */
  after: KbProbeSide | null;
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
/** #355: result of POST /kb/collections/:id/sync — the enqueue ack + the last
 * KNOWN commit (the new one isn't known until the async job finishes). */
export type SyncResult = { status: string; git_last_sha: string | null };

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
export type CollectionOptions = {
  useRag?: boolean;
  useWiki?: boolean;
  /** #355: code-collection wiring. A non-empty `gitUrl` makes this a code
   * collection — the backend clones it and builds the wiki from source. `gitToken`
   * is write-only (never echoed back). */
  gitUrl?: string;
  gitBranch?: string;
  gitToken?: string;
};

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
  /** Issue #39 Q11: short progress / error line beside the status — now the
   * exception summary on `status="error"`. Empty while indexing (the racy
   * per-page string was dropped in #248 — use the unit counts below). */
  status_detail?: string;
  /** #248: real fan-out progress — units (e.g. PDF pages) done / total. Both 0
   * (or absent) for a single-job / ready doc, which means "no progress bar".
   * Monotonic while indexing. */
  units_done?: number;
  units_total?: number;
  /** Indexed chunk count + how many times this doc was cited. */
  chunks?: number;
  cited?: number;
  /** Stored blob size in bytes + the resource's last-update time (epoch ms). */
  size?: number;
  updated_at?: number;
  /** #105: the AI quality grade (0–100) of this doc as a knowledge source, or
   * null/absent when un-scored (no rubric / not yet judged). Drives the quality
   * badge + the "sort by quality" control. `quality_rationale` ("why good/bad")
   * rides the row so the doc IDE's status bar shows it without a render call;
   * the per-dimension breakdown stays on `KbRenderedDoc`. */
  quality_score?: number | null;
  quality_rationale?: string;
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
  /** #105: the AI quality verdict shown when the doc is open — holistic 0–100
   * score (null = un-scored), the rationale ("why good/bad"), and the
   * per-dimension breakdown (keys named by the collection's rubric). */
  quality_score?: number | null;
  quality_rationale?: string;
  quality_breakdown?: Record<string, number>;
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
  /** #254 — aggregated source location ({ page: [3, 4], section: ["Ch.2 > 2.1"] });
   * distinct values per locator. Absent/empty when the source had none. */
  provenance?: Provenance;
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
  /** #334: per-message cap on how many times this reply runs kb_search.
   * 0 = don't search this reply; omitted = operator default. The BE clamps
   * to the operator's ceiling. */
  maxKbSearches?: number;
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

/** #175 自動 context card — where a proposed card came from (the audit "依據"). */
export type KbCardProvenance = { doc_id: string; path: string; snippet: string };

/** #175 — one reviewable card proposal off a generation job's artifact. `mode`
 * is `new` or `update` (then `target_card_id` is the card to overwrite);
 * `confident=false` marks an uncertain draft (⚠️, defaulted out of commit);
 * `decision` is the reviewer's verdict, persisted on the job (resumable). */
export type KbProposedCard = {
  keys: string[];
  title: string;
  body: string;
  confident: boolean;
  mode: "new" | "update";
  target_card_id: string | null;
  provenance: KbCardProvenance[];
  decision: "pending" | "accepted" | "rejected";
};

/** #175 — a generation run's status + its current proposals. */
export type KbCardGenStatus = {
  status: "pending" | "processing" | "completed" | "failed";
  proposals: KbProposedCard[];
};

/** #175 — the tallies returned by committing a run's accepted proposals. */
export type KbCardGenCommit = { created: number; updated: number; skipped: number };

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
      /** #105: the per-collection quality rubric (what makes a doc a good/bad
       * knowledge source + which dimensions to assess). Blank = not scored. */
      quality_rubric?: string;
      /** #328: the per-collection parser guidance — the findability modal's
       * "Apply" persists the tuned guidance here. */
      parser_guidance?: string;
      /** #355: edit a code collection's git connection. `git_branch` (null/"" ⇒
       * remote default); `git_token` is write-only — send it ONLY to rotate the
       * stored PAT (omit to keep the current one). */
      git_branch?: string | null;
      git_token?: string;
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
    page?: { offset?: number; limit?: number; sort?: "recent" | "quality" },
  ): Promise<KbDocumentsPage>;
  /** Multipart upload; returns the ingested document ids (one per archive
   * member). `path` overrides the stored filename — used for folder uploads to
   * preserve each file's relative path. */
  uploadDocument(collectionId: string, file: File, path?: string): Promise<string[]>;
  /** #325: the browser-runnable upload-check descriptors. The FE screens
   * picked files against these before upload, pre-blocking the common case
   * (an encrypted Office file) without a round-trip. */
  listUploadChecks(): Promise<UploadCheckHint[]>;
  /** Issue #101: build the collection's export zip and return its handle. The
   * zip is held server-side until streamed (or reaped); two-step so a large
   * export's build latency hides behind a loading state, not a stalled click. */
  prepareCollectionDownload(collectionId: string): Promise<DownloadPrepared>;
  /** The URL to navigate to (native streaming download) for a prepared export.
   * Not a fetch — an `<a href>` so the browser streams straight to disk. */
  streamCollectionDownloadUrl(collectionId: string, downloadId: string): string;
  /** Issue #247: build a raw (no-manifest) zip of the docs under `prefix`
   * (`""` = the whole collection) and return its handle. */
  prepareFolderDownload(collectionId: string, prefix: string): Promise<DownloadPrepared>;
  /** Issue #247: the `<a href>` URL to stream a prepared folder zip; `prefix` is
   * echoed so the streamed file is named after the folder. */
  folderDownloadUrl(collectionId: string, downloadId: string, prefix: string): string;
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
  /** #328 findability probe: rank a doc's content for a question (`before`) and,
   * when a candidate `guidance` is given, a non-persisted re-parse of the doc
   * (`after`). Read-only — nothing is written. */
  probeFindability(body: {
    doc_id: string;
    question: string;
    guidance?: string | null;
    depth?: number;
  }): Promise<KbProbeResult>;
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
  /** #355: re-sync a CODE collection — enqueues an async clone+ingest+build job
   * (returns immediately with status="queued"). Progress + failures surface via
   * `getWikiStatus`; the new commit shows once the job finishes. */
  syncCollection(collectionId: string): Promise<SyncResult>;

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

  /** #175 自動 context card: start a generation run over the selected documents;
   * returns the job id to poll. */
  generateContextCards(collectionId: string, docIds: string[]): Promise<string>;
  /** Poll a generation run — its status + current proposals (resumable). */
  getCardGenStatus(jobId: string): Promise<KbCardGenStatus>;
  /** Persist the reviewer's edited / decided proposals back onto the run. */
  reviewCardGen(jobId: string, proposals: KbProposedCard[]): Promise<KbCardGenStatus>;
  /** Commit the run's accepted proposals to real cards; returns the tallies. */
  commitCardGen(jobId: string): Promise<KbCardGenCommit>;

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
          // #355: code-collection git wiring (omit empties so a plain collection
          // sends none of them).
          ...(opts?.gitUrl ? { git_url: opts.gitUrl } : {}),
          ...(opts?.gitBranch ? { git_branch: opts.gitBranch } : {}),
          ...(opts?.gitToken ? { git_token: opts.gitToken } : {}),
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
  async probeFindability(body) {
    const resp = await apiFetch(`/kb/findability/probe`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    });
    return (await ok(resp, "probe findability")).json();
  },
  async listDocuments(collectionId, page) {
    const qs = new URLSearchParams();
    if (page?.offset != null) qs.set("offset", String(page.offset));
    if (page?.limit != null) qs.set("limit", String(page.limit));
    if (page?.sort != null) qs.set("sort", page.sort);
    const path = `/kb/collections/${encodeURIComponent(collectionId)}/documents`;
    const url = qs.size ? `${path}?${qs.toString()}` : path;
    const resp = await apiFetch(url);
    return (await ok(resp, "list documents")).json();
  },
  async uploadDocument(collectionId, file, path) {
    const form = new FormData();
    form.append("file", file, path ?? file.name);
    const resp = await apiFetch(`/kb/collections/${encodeURIComponent(collectionId)}/documents`, {
      method: "POST",
      body: form,
    });
    // #325: a refused file comes back as a structured 422 — surface it as a
    // typed error the UI can route to its "can't accept" list, distinct from
    // a generic upload failure.
    if (resp.status === 422) {
      const detail = await resp
        .json()
        .then((b) => b?.detail as { check_id?: string; reason_code?: string; message_key?: string })
        .catch(() => null);
      if (detail?.message_key) {
        throw new UploadBlockedError(detail.message_key, detail.check_id, detail.reason_code);
      }
    }
    return (await ok(resp, "upload document")).json().then((b) => b.document_ids);
  },
  async listUploadChecks() {
    return (await ok(await apiFetch("/kb/upload-checks"), "list upload checks")).json();
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
  async prepareFolderDownload(collectionId, prefix) {
    const resp = await ok(
      await apiFetch(
        `/kb/collections/${encodeURIComponent(collectionId)}/folder-download/prepare?prefix=${encodeURIComponent(prefix)}`,
        { method: "POST" },
      ),
      "prepare folder download",
    );
    return resp.json();
  },
  folderDownloadUrl(collectionId, downloadId, prefix) {
    return `${API_PREFIX}/kb/collections/${encodeURIComponent(collectionId)}/folder-download/${encodeURIComponent(downloadId)}?prefix=${encodeURIComponent(prefix)}`;
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
  async syncCollection(collectionId) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/sync`;
    return (await ok(await apiFetch(url, { method: "POST" }), "sync collection")).json();
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

  async generateContextCards(collectionId, docIds) {
    const url = `/kb/collections/${encodeURIComponent(collectionId)}/context-cards/generate`;
    const resp = await ok(
      await apiFetch(url, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ doc_ids: docIds }),
      }),
      "generate context cards",
    );
    return (await resp.json()).job_id;
  },
  async getCardGenStatus(jobId) {
    const url = `/kb/context-card-gen/${encodeURIComponent(jobId)}`;
    return (await ok(await apiFetch(url), "card gen status")).json();
  },
  async reviewCardGen(jobId, proposals) {
    const url = `/kb/context-card-gen/${encodeURIComponent(jobId)}/review`;
    return (
      await ok(
        await apiFetch(url, {
          method: "POST",
          headers: jsonHeaders,
          body: JSON.stringify({ proposals }),
        }),
        "review card gen",
      )
    ).json();
  },
  async commitCardGen(jobId) {
    const url = `/kb/context-card-gen/${encodeURIComponent(jobId)}/commit`;
    return (await ok(await apiFetch(url, { method: "POST" }), "commit card gen")).json();
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
        max_kb_searches: args.maxKbSearches,
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
