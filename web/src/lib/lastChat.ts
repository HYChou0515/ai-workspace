/**
 * The last chat a user opened, remembered PER App (#chat-private) so entering a
 * chat-first App resumes that App's own last conversation — opencode-style
 * "continue where you left off", scoped to the App rather than one global chat.
 * Per-device (localStorage); a stale/removed id is validated away by the caller.
 */

export const lastChatKey = (slug: string) => `chat:last:${slug}`;

export function rememberLastChat(slug: string, itemId: string): void {
  try {
    localStorage.setItem(lastChatKey(slug), itemId);
  } catch {
    /* privacy mode / disabled storage — resume just won't happen */
  }
}

export function recallLastChat(slug: string): string | null {
  try {
    return localStorage.getItem(lastChatKey(slug));
  } catch {
    return null;
  }
}
