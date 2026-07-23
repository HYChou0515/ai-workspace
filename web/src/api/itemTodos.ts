/**
 * #613: one chat's todo checklist — the pinned panel's REST surface.
 *
 * GET hydrates; PUT is a whole-list REPLACE (the same semantics as the agent's
 * `update_todos` tool; the panel locks editing while a turn streams so the two
 * writers never interleave). Live mid-turn updates ride the chat stream as
 * `todos_updated` events — this client is only hydration + user edits.
 * `client` is injectable so the panel unit-tests against a fake.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

export type TodoStatus = "pending" | "in_progress" | "completed";
export type TodoItem = { text: string; status: TodoStatus };

export type ItemTodosApi = {
  getTodos(slug: string, itemId: string, chatId: string): Promise<TodoItem[]>;
  putTodos(
    slug: string,
    itemId: string,
    chatId: string,
    items: TodoItem[],
  ): Promise<TodoItem[]>;
};

const todosUrl = (slug: string, itemId: string, chatId: string) =>
  `/a/${enc(slug)}/items/${enc(itemId)}/chats/${enc(chatId)}/todos`;

export const itemTodosApi: ItemTodosApi = {
  async getTodos(slug, itemId, chatId) {
    const r = await apiFetch(todosUrl(slug, itemId, chatId));
    return ((await r.json()) as { items: TodoItem[] }).items;
  },
  async putTodos(slug, itemId, chatId, items) {
    const r = await apiFetch(todosUrl(slug, itemId, chatId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    return ((await r.json()) as { items: TodoItem[] }).items;
  },
};
