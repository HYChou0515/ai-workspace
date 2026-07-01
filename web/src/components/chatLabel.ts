import type { ItemChatSummary } from "../api/itemChats";

/**
 * A stable, human-readable label for an unnamed chat (#190). Earlier we showed a
 * literal "New chat", which read like the create button sitting right next to it.
 * Instead we name the chat by its creation time — derived from the immutable
 * `created_ms`, so it never reshuffles as sibling chats come and go. A chat with
 * no creation time yet (shouldn't normally happen) falls back to a plain "Chat".
 */
export function untitledChatLabel(created_ms: number | null): string {
  if (created_ms == null) return "Chat";
  const d = new Date(created_ms);
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `Chat · ${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${mm}`;
}

/**
 * A chat's display name (#132): the explicit title (set by rename or a workflow
 * launch), else the first-message hint, else a creation-time label (#190). There
 * is no "main chat" any more, so the default chat gets no special label.
 */
export function chatLabel(chat: ItemChatSummary): string {
  return chat.title || chat.name_hint || untitledChatLabel(chat.created_ms);
}
