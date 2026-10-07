import { createContext, useContext } from "react";

/**
 * The item a chat's log belongs to — what a card in the log needs to act on
 * THIS item (the `show_skill_hub_entry` card's 〔安裝〕). Provided by the item
 * chat panel (`AgentPanel`); a log drawn anywhere else (the knowledge-base
 * chat, a read-only replay) has none, and such a card shows without actions.
 */
export type ChatItem = { slug: string; itemId: string };

const ChatItemContext = createContext<ChatItem | null>(null);

export const ChatItemProvider = ChatItemContext.Provider;

export function useChatItem(): ChatItem | null {
  return useContext(ChatItemContext);
}
