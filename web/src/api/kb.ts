/**
 * KB (knowledge-base chatbot) API client — collections, documents, chat
 * threads, and the streaming chat turn. Separate from the investigation
 * `ApiClient`: the KB is its own subsystem. Mock/real swap on the same
 * `VITE_USE_MOCK` switch as `./index`.
 *
 * Wire shapes mirror `api/kb_routes.py` + `api/kb_chat_routes.py`.
 */

import type { AgentEvent } from "../events";
import { mockKbApi } from "./kbMock";
import { parseSseStream } from "./sse";

export type KbCollection = {
  resource_id: string;
  name: string;
  description: string;
};

export type KbDocument = {
  resource_id: string;
  path: string;
  content_type: string;
};

/** A document rendered for the citation viewer: markdown with kb:// links. */
export type KbRenderedDoc = {
  filename: string;
  collection_id: string;
  markdown: string;
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
  role: "user" | "assistant" | "tool";
  content: string;
  reasoning: string | null;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_call_id: string | null;
  created_at: number | null;
  citations: KbCitation[];
};

export type KbChatSummary = {
  resource_id: string;
  title: string;
  collection_ids: string[];
  message_count: number;
};

export type KbChatDetail = {
  resource_id: string;
  title: string;
  collection_ids: string[];
  messages: KbChatMessage[];
};

export type SendKbMessageArgs = {
  chatId: string;
  content: string;
  signal?: AbortSignal;
};

export interface KbApi {
  listCollections(): Promise<KbCollection[]>;
  createCollection(name: string, description?: string): Promise<KbCollection>;
  listDocuments(collectionId: string): Promise<KbDocument[]>;
  /** Multipart upload; returns the ingested document ids (one per archive member). */
  uploadDocument(collectionId: string, file: File): Promise<string[]>;
  /** Render a source document to markdown (kb:// links) for the citation viewer. */
  renderDocument(documentId: string): Promise<KbRenderedDoc>;

  listChats(): Promise<KbChatSummary[]>;
  createChat(title: string, collectionIds: string[]): Promise<KbChatSummary>;
  getChat(chatId: string): Promise<KbChatDetail>;
  deleteChat(chatId: string): Promise<void>;
  /** Stream one chat turn. Citations are not in the stream — refetch the chat
   * on done to get the persisted assistant message with its [n] resolved. */
  streamMessage(args: SendKbMessageArgs): AsyncGenerator<AgentEvent>;
}

/* ------------------------------- real ------------------------------- */

async function ok(resp: Response, what: string): Promise<Response> {
  if (!resp.ok) throw new Error(`${what} failed: ${resp.status}`);
  return resp;
}

const jsonHeaders = { "content-type": "application/json" };

export const realKbApi: KbApi = {
  async listCollections() {
    return (await ok(await fetch("/kb/collections"), "list collections")).json();
  },
  async createCollection(name, description = "") {
    const resp = await ok(
      await fetch("/kb/collections", {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ name, description }),
      }),
      "create collection",
    );
    return resp.json();
  },
  async listDocuments(collectionId) {
    const resp = await fetch(`/kb/collections/${encodeURIComponent(collectionId)}/documents`);
    return (await ok(resp, "list documents")).json();
  },
  async uploadDocument(collectionId, file) {
    const form = new FormData();
    form.append("file", file);
    const resp = await ok(
      await fetch(`/kb/collections/${encodeURIComponent(collectionId)}/documents`, {
        method: "POST",
        body: form,
      }),
      "upload document",
    );
    return (await resp.json()).document_ids;
  },
  async renderDocument(documentId) {
    // documentId is a path-shaped id ({collection}/{user}/{path}); the route is
    // {doc_id:path}, so encode each segment but keep the slashes.
    const encoded = documentId.split("/").map(encodeURIComponent).join("/");
    return (await ok(await fetch(`/kb/documents/${encoded}`), "render document")).json();
  },

  async listChats() {
    return (await ok(await fetch("/kb/chats"), "list chats")).json();
  },
  async createChat(title, collectionIds) {
    const resp = await ok(
      await fetch("/kb/chats", {
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
    return (await ok(await fetch(`/kb/chats/${encodeURIComponent(chatId)}`), "get chat")).json();
  },
  async deleteChat(chatId) {
    await ok(
      await fetch(`/kb/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" }),
      "delete chat",
    );
  },
  async *streamMessage(args) {
    const resp = await fetch(`/kb/chats/${encodeURIComponent(args.chatId)}/messages`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ content: args.content }),
      signal: args.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`kb message failed: ${resp.status}`);
    yield* parseSseStream(resp.body);
  },
};

/* ----------------------------- selector ----------------------------- */

const useMock = import.meta.env.VITE_USE_MOCK === "1";

export const kbApi: KbApi = useMock ? mockKbApi : realKbApi;
