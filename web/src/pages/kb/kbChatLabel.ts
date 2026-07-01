import type { KbChatSummary } from "../../api/kb";
import { untitledChatLabel } from "../../components/chatLabel";

/**
 * A KB chat's display name (#357), mirroring the topic-hub `chatLabel` (#132):
 * the explicit title (set by rename) → the first-message hint (name_hint) → a
 * timestamp label. So an unnamed thread is still tellable apart in the list
 * instead of rendering blank.
 */
export function kbChatLabel(chat: Pick<KbChatSummary, "title" | "name_hint" | "updated_ms">): string {
  return chat.title || chat.name_hint || untitledChatLabel(chat.updated_ms ?? null);
}
