/**
 * Multi-chat API client (topic-hub §3) — an item holds many chats (free + workflow).
 * These chat-scoped calls parallel the item-level ones in `real.ts`; the backend's
 * default-chat reconciliation means addressing the default chat here streams on the
 * item's broadcast stream, so the FE can treat every chat uniformly by its id.
 *
 * A turn uses the #43 broadcast model: `sendMessage` POSTs to ENQUEUE (202, no body),
 * and the turn's events arrive on the long-lived `subscribe` stream — same as
 * `useAgent`, just keyed on a chat id. `client` is injectable so hooks unit-test
 * against a fake.
 */

import type { BodyEnhancements } from "../lib/kbEnhancementMode";
import type { AgentEvent } from "../events";
import { apiFetch, HttpError } from "./http";
import { parseSseStream } from "./sse";
import type { Message } from "./types";

const enc = encodeURIComponent;
const base = (slug: string, itemId: string) => `/a/${enc(slug)}/items/${enc(itemId)}`;

/** One chat in an item's list (GET /chats). */
export type ItemChatSummary = {
  chat_id: string;
  title: string;
  run_id: string | null;
  created_ms: number | null;
  message_count: number;
  is_default: boolean;
  /** First user message (truncated) — the display fallback for an unnamed chat (#132). */
  name_hint: string;
  /** The driving workflow run's status for a workflow chat, else null (#132). */
  status: string | null;
  /** Epoch ms of the chat's last write — the recency sort key (#132). */
  last_activity_ms: number | null;
};

/** A hydrated chat thread (persisted messages — carries resolved [n] citations). */
export type ItemChat = {
  chatId: string;
  title: string;
  runId: string | null;
  messages: Message[];
};

type ConversationEnvelope = {
  data: { title?: string; run_id?: string | null; messages?: Message[] };
  revision_info: { resource_id: string };
};

async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
  if (!r.ok) throw new HttpError(r.status, `${what} failed: ${r.status}`);
  return r.json() as Promise<T>;
}

export const itemChatApi = {
  async listChats(slug: string, itemId: string): Promise<ItemChatSummary[]> {
    return jsonOrThrow(await apiFetch(`${base(slug, itemId)}/chats`), "list chats");
  },

  async createChat(slug: string, itemId: string, title = ""): Promise<ItemChatSummary> {
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/chats`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title }),
      }),
      "create chat",
    );
  },

  /** Rename a chat (#132) — set its display title from the manage modal. */
  async renameChat(
    slug: string,
    itemId: string,
    chatId: string,
    title: string,
  ): Promise<ItemChatSummary> {
    return jsonOrThrow(
      await apiFetch(`${base(slug, itemId)}/chats/${enc(chatId)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title }),
      }),
      "rename chat",
    );
  },

  /** Delete a chat (#132) — the backend also cancels a running workflow first. */
  async deleteChat(slug: string, itemId: string, chatId: string): Promise<void> {
    const r = await apiFetch(`${base(slug, itemId)}/chats/${enc(chatId)}`, { method: "DELETE" });
    if (!r.ok) throw new HttpError(r.status, `delete chat failed: ${r.status}`);
  },

  /** Hydrate a chat's persisted thread via the specstar single-resource route. */
  async getChat(_slug: string, _itemId: string, chatId: string): Promise<ItemChat> {
    const e = await jsonOrThrow<ConversationEnvelope>(
      await apiFetch(`/conversation/${enc(chatId)}`),
      "get chat",
    );
    return {
      chatId: e.revision_info.resource_id,
      title: e.data.title ?? "",
      runId: e.data.run_id ?? null,
      messages: e.data.messages ?? [],
    };
  },

  async sendMessage(args: {
    slug: string;
    itemId: string;
    chatId: string;
    content: string;
    /** grill-me: the `ask_user` question this message answers. */
    answers?: string;
    reasoningEffort?: string;
    /** Knowledge-search depth + the "Search the wiki" toggle for this turn's
     * ask_knowledge_base lookups — mirrors the item-level `api.sendMessage`. The
     * chat-scoped backend forwards `body.enhancements`. */
    enhancements?: BodyEnhancements;
    /** #537: how many times this turn's ask_knowledge_base lookups may search the
     * documents in total. This surface never sent it, so the composer's stepper
     * was inert here and the operator default always won — including when the
     * user set 0. */
    maxKbSearches?: number;
    /** #537 follow-up: the wiki twin — caps this turn's ask_knowledge_base wiki
     * consults in total. Same dropped-at-the-POST hazard as `answers` above. */
    maxWikiSearches?: number;
    /** #605: per-chat disclosure toggle (see kb.ts SendMessageArgs). */
    disclosure?: boolean;
    /** #380: skills the user queued to apply THIS turn (one-shot, hard-preloaded).
     * Mirrors the item-level `api.sendMessage`; the chat endpoint shares the body. */
    applySkills?: string[];
    /** Attached image workspace paths — a VLM main model reads them inline
     * (no read_image round-trip). Mirrors the item-level `api.sendMessage`. */
    imagePaths?: string[];
    signal?: AbortSignal;
  }): Promise<void> {
    // #43 broadcast model: POST enqueues (202); events arrive on `subscribe`.
    const r = await apiFetch(`${base(args.slug, args.itemId)}/chats/${enc(args.chatId)}/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        content: args.content,
        reasoning_effort: args.reasoningEffort,
        enhancements: args.enhancements,
        max_kb_searches: args.maxKbSearches,
        max_wiki_searches: args.maxWikiSearches,
        disclosure: args.disclosure,
        apply_skills: args.applySkills,
        image_paths: args.imagePaths,
        answers: args.answers,
      }),
      signal: args.signal,
    });
    if (!r.ok) throw new HttpError(r.status, `send failed: ${r.status}`);
  },

  async *subscribe(
    slug: string,
    itemId: string,
    chatId: string,
    signal?: AbortSignal,
    since?: number,
  ): AsyncGenerator<AgentEvent> {
    // `since` (a reconnect) resumes the same-pod replay buffer from that seq.
    const q = since !== undefined ? `?since=${since}` : "";
    const r = await apiFetch(`${base(slug, itemId)}/chats/${enc(chatId)}/stream${q}`, { signal });
    if (!r.ok || !r.body) throw new HttpError(r.status, `stream failed: ${r.status}`);
    yield* parseSseStream(r.body) as AsyncGenerator<AgentEvent>;
  },

  async cancelMessage(slug: string, itemId: string, chatId: string): Promise<void> {
    // Idempotent on the BE; swallow noise so a double-click on Stop is quiet.
    await apiFetch(`${base(slug, itemId)}/chats/${enc(chatId)}/messages/current`, {
      method: "DELETE",
    }).catch(() => undefined);
  },

  /** Undo the last `turns` whole turns of THIS chat (#38, chat-scoped twin of
   * `api.undoTurns`); the FE re-snapshots the thread after. */
  async undoTurns(slug: string, itemId: string, chatId: string, turns: number): Promise<void> {
    const r = await apiFetch(
      `${base(slug, itemId)}/chats/${enc(chatId)}/messages?turns=${turns}`,
      { method: "DELETE" },
    );
    if (!r.ok) throw new HttpError(r.status, `undo failed: ${r.status}`);
  },

  /** @mention people to "come look" — notifies them, does NOT run the agent.
   * Item-level (mentions are per-item, not per-chat); mirrors `api.addMention`. */
  async mention(slug: string, itemId: string, userIds: string[], note: string): Promise<void> {
    const r = await apiFetch(`${base(slug, itemId)}/mentions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ user_ids: userIds, note }),
    });
    if (!r.ok) throw new HttpError(r.status, `mention failed: ${r.status}`);
  },
};

export type ItemChatApi = typeof itemChatApi;
