import { createContext, useContext } from "react";

/**
 * The item a chat's log belongs to — what a card in the log needs to act on
 * THIS item (the `show_skill_hub_entry` card's 〔安裝〕). Provided by the item
 * chat panel (`AgentPanel`); a log drawn anywhere else (the knowledge-base
 * chat, a read-only replay) has none, and such a card shows without actions.
 */
export type ChatItem = { slug: string; itemId: string; env?: ChatEnv };

/** Where the panel opens to, from a card (docs/plan-env-request-card.md):
 * the variable to show, the login to go straight into (or `null` for a
 * field), and the tab — "mine" unless only the shared copy can fix it (N4). */
export type EnvTarget = { name: string; login: string | null; tab: "mine" | "shared" };

/** What the `request_env` card needs from the chat it sits in: the item's
 * shared values and policy (the viewer's own are read by the card), a way to
 * open the environment panel, and a way to send the user's Retry. Absent on a
 * chat that cannot do those — the card then shows without actions. */
export type ChatEnv = {
  shared: Record<string, string>;
  policy: Record<string, string>;
  open: (target: EnvTarget) => void;
  retry: (text: string) => void;
};

const ChatItemContext = createContext<ChatItem | null>(null);

export const ChatItemProvider = ChatItemContext.Provider;

export function useChatItem(): ChatItem | null {
  return useContext(ChatItemContext);
}
