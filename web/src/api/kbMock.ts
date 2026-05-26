/**
 * In-memory KB API for dev/tests (VITE_USE_MOCK=1). Mirrors the real client's
 * observable behavior: create/list collections, upload→list documents, chat
 * thread CRUD, and a scripted streaming turn whose persisted answer carries a
 * citation (so the UI's citation path is exercisable without a backend).
 */

import type { AgentEvent } from "../events";
import type {
  KbApi,
  KbChatDetail,
  KbChatMessage,
  KbChatSummary,
  KbCollection,
  KbDocChunk,
  KbDocument,
  KbRenderedDoc,
  SendKbMessageArgs,
} from "./kb";

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${(++seq).toString(36)}`;

const collections = new Map<string, KbCollection>();
const documents = new Map<string, KbDocument[]>();
const docChunks = new Map<string, KbDocChunk[]>();
const chats = new Map<string, KbChatDetail>();

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
    return {
      name: "KB Agent",
      suggestions: ["What does the knowledge base say about this?", "Find related past findings"],
    };
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
  async createCollection(name, description = "") {
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
  async listDocuments(collectionId) {
    return documents.get(collectionId) ?? [];
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
  async renderDocument(documentId): Promise<KbRenderedDoc> {
    const filename = documentId.split("/").pop() ?? documentId;
    return {
      filename,
      collection_id: documentId.split("/")[0] ?? "",
      markdown: `# ${filename}\n\nMock document body for **${documentId}**.`,
    };
  },
  async getDocChunks(documentId) {
    return [...(docChunks.get(documentId) ?? [])].sort((a, b) => a.seq - b.seq);
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

/** Internal — reset between tests. */
export const _resetKbMock = () => {
  collections.clear();
  documents.clear();
  docChunks.clear();
  chats.clear();
  seq = 0;
};
