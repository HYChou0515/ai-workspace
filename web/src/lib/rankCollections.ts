import type { KbChatSummary, KbCollection } from "../api/kb";

/**
 * Order collections by how likely the user wants to search them (#271), so the
 * KB chat can surface a short pill shortlist of the top few. A transparent,
 * deterministic lexicographic sort (NOT a weighted score, which is hard to tune
 * and test). Keys, in order:
 *
 *   1. personal usage frequency — how many of the user's own chats searched this
 *      collection (read from each chat's `collection_ids`);
 *   2. ownership — collections the user owns come before others;
 *   3. `cited` — how often the collection's docs have been cited in answers;
 *   4. `doc_count` — bigger collections (more likely to hold the answer);
 *   5. name — a final A→Z tiebreak so the order is fully stable.
 *
 * Cold start (no chats) leaves every frequency at 0, so a brand-new user falls
 * through to cited → doc_count and still sees the most-cited / largest first.
 */
export function rankCollections(
  collections: KbCollection[],
  chats: KbChatSummary[],
  me: string,
): KbCollection[] {
  // "Personal" usage = the user's own chats. A chat with no recorded owner
  // (older threads / tests) counts too, since listChats only ever returns the
  // current user's own + shared-with-them threads.
  const mine = chats.filter((c) => c.owner == null || c.owner === me);
  const freq = new Map<string, number>();
  for (const c of mine) {
    for (const id of c.collection_ids) freq.set(id, (freq.get(id) ?? 0) + 1);
  }

  const key = (c: KbCollection) => [
    freq.get(c.resource_id) ?? 0,
    c.owner === me ? 1 : 0,
    c.cited,
    c.doc_count,
  ];

  return [...collections].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    for (let i = 0; i < ka.length; i++) {
      if (ka[i] !== kb[i]) return kb[i] - ka[i]; // all four keys: higher first
    }
    return a.name.localeCompare(b.name); // stable A→Z tiebreak
  });
}
