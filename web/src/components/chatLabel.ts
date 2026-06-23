import type { ItemChatSummary } from "../api/itemChats";

/**
 * A chat's display name (#132): the explicit title (set by rename or a workflow
 * launch), else the first-message hint, else a generic placeholder. There is no
 * "main chat" any more, so the default chat gets no special label.
 */
export function chatLabel(chat: ItemChatSummary): string {
  return chat.title || chat.name_hint || "New chat";
}
