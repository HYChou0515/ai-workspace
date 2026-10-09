import { createContext, useContext } from "react";

/**
 * The item a chat's log belongs to — what a card in the log needs to act on
 * THIS item (the `show_skill_hub_entry` card's 〔安裝〕). Provided by the item
 * chat panel (`AgentPanel`); a log drawn anywhere else (the knowledge-base
 * chat, a read-only replay) has none, and such a card shows without actions.
 */
export type ChatItem = {
  slug: string;
  itemId: string;
  env?: ChatEnv;
  /** The chat the log is, when it is not the item's default one — where the
   * "請幫我查" card's answer goes (docs/plan-outside-lookup.md). */
  chatId?: string;
  /** `false` when this viewer may not add files (`add_content`): the
   * 請幫我查 card then offers no attachments, and its text is not saved. */
  canAddFiles?: false;
  /** Whether a message in the thread already answers this call
   * (`Message.answers`), so an answered card stays retired after a reload. */
  answered?: (callId: string) => boolean;
};

/** Where the panel opens to, from a card (docs/plan-env-request-card.md): the
 * variable to show, always on the person's own tab (N4) — they switch tabs
 * themselves. A login opens its own page instead (`EnvLoginModal`, N6). */
export type EnvTarget = { name: string };

/** What the `request_env` card needs from the chat it sits in: the item's
 * shared values and policy (the viewer's own are read by the card), a way to
 * open the environment panel, and a way to send the user's Retry and know it
 * was sent. Absent on a
 * chat that cannot do those — the card then shows without actions. */
export type ChatEnv = {
  shared: Record<string, string>;
  policy: Record<string, string>;
  open: (target: EnvTarget) => void;
  /** Send the Retry as a message that answers this card's call; `false` when
   * the chat refused to send (the composer says why). */
  retry: (callId: string, text: string) => boolean;
  /** Whether a message in the thread already answers this call — so a card
   * that was retried stays retired after a reload. */
  answered: (callId: string) => boolean;
};

const ChatItemContext = createContext<ChatItem | null>(null);

export const ChatItemProvider = ChatItemContext.Provider;

export function useChatItem(): ChatItem | null {
  return useContext(ChatItemContext);
}
