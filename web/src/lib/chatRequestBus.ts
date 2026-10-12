/**
 * "Open a new chat about this, with these words in its box."
 *
 * An execution's tab offers "Open a chat with these results" and, on a failed
 * step, "Ask AI why it failed" (plan-workflow-out-of-chat D8 / D9). The tab is
 * in the editor area and the chat beside it; neither is the other's ancestor, so
 * the ask travels on a bus keyed by item (`scopedBus`). The item's chat shell
 * opens a fresh chat and hands the text to its composer (`agentDraftBus`) — a
 * draft, not a send: what to ask is still the person's to decide, and the
 * workflow's own conversation is never the one continued (D3).
 */

import { createScopedBus } from "./scopedBus";

const bus = createScopedBus<string>();

/** Subscribe to requests for a new chat on this item, each carrying its draft. */
export const subscribeChatRequest = bus.subscribe;

/** Ask the item's chat for a new chat whose composer holds `draft`. */
export const publishChatRequest = bus.publish;

/** Whether the item's chat is mounted to answer (not on the editor-area page). */
export const canRequestChat = bus.has;
