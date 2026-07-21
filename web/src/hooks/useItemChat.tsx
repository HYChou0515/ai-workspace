import { useMemo } from "react";

import { itemChatApi, type ItemChatApi } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import { getKbSearchMax } from "../lib/kbSearchMax";
import {
  getStored as getKbEnhancementSelection,
  toBodyEnhancements,
} from "../lib/kbEnhancementMode";
import { getReasoningEffort } from "../lib/reasoningEffort";
import type { AgentState } from "./useAgent";
import {
  type BroadcastChatTransport,
  STORE_POLL_MS,
  useChatSession,
} from "./useChatSession";

/**
 * Drives ONE chat of an item (topic-hub §3) — a free chat or a workflow chat.
 *
 * The whole turn state machine (hydrate / subscribe+reconnect / fold / terminal
 * re-snapshot / store-poll / send / cancel / undo / mention) lives in
 * {@link useChatSession}, shared with `useAgent`; this hook only says WHICH
 * endpoints a named chat talks to. `client` stays injectable so the transport is
 * unit-testable against a fake.
 *
 * Returns the same shape as `useAgent` (`AgentState`) so the full `AgentPanel`
 * renders against a chat — model picker, suggestions, @mention, attach, undo and
 * Cmd-Enter all work per chat. `investigationId` is the ITEM id (the file /
 * attach / replay APIs are item-scoped, shared across the item's chats).
 */
export type UseItemChat = AgentState & { chatId: string };

export function useItemChat({
  slug,
  itemId,
  chatId,
  client = itemChatApi,
  pollMs = STORE_POLL_MS,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  client?: ItemChatApi;
  /** Store-poll fallback cadence (#202); overridable in tests. */
  pollMs?: number;
}): UseItemChat {
  const transport = useMemo<BroadcastChatTransport>(
    () => ({
      threadKey: chatId,
      queryKey: qk.itemChat(slug, itemId, chatId),
      // File edits are item-scoped: every chat of the item shares one workspace.
      filesKey: qk.files(itemId),
      getThread: () => client.getChat(slug, itemId, chatId),
      subscribe: (signal) => client.subscribe(slug, itemId, chatId, signal),
      post: (content, opts) =>
        client.sendMessage({
          slug,
          itemId,
          chatId,
          content,
          reasoningEffort: getReasoningEffort() ?? undefined,
          // Knowledge-search depth for this turn's ask_knowledge_base lookups.
          enhancements: toBodyEnhancements(getKbEnhancementSelection()),
          // #537: the turn-wide document-search allowance. This surface never sent
          // it, so the composer's stepper moved but nothing changed — the operator
          // default applied regardless, including when the user chose 0.
          maxKbSearches: getKbSearchMax(),
          // #380: skills queued in the Skills panel to apply THIS turn (one-shot).
          applySkills: opts?.applySkills,
          // Attached image workspace paths — a VLM main model reads them inline.
          imagePaths: opts?.imagePaths,
          // grill-me: the `ask_user` question this message answers. This is the
          // surface the workspace actually uses, so dropping it here left the
          // field set everywhere except on the wire.
          answers: opts?.answers,
        }),
      requestCancel: () => client.cancelMessage(slug, itemId, chatId),
      undoTurns: (turns) => client.undoTurns(slug, itemId, chatId, turns),
      // A mention notifies the ITEM, not the chat.
      addMention: (userIds, note) => client.mention(slug, itemId, userIds, note),
    }),
    [slug, itemId, chatId, client],
  );

  const session = useChatSession(transport, pollMs);
  return { investigationId: itemId, chatId, ...session };
}
