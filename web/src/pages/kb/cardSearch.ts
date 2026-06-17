/**
 * Front-end mirror of the backend context-card matching (kb/context_cards.py),
 * so the Context Cards tab can preview both lookups over the already-loaded list
 * without a round-trip. Keep `normKey` in sync with the backend `norm()`.
 */
import type { KbContextCard } from "../../api/kb";

/** NFKC → lower → collapse whitespace. The one normalisation every lookup
 * shares (mirrors the backend `norm()` and `kbMock`). */
export const normKey = (s: string): string =>
  s.normalize("NFKC").toLowerCase().split(/\s+/).filter(Boolean).join(" ");

/** Exact key lookup — the deterministic get(term): cards carrying the query as
 * an exact normalised key (so "M4" never matches an "M40" card). Empty → all. */
export function lookupByName(query: string, cards: KbContextCard[]): KbContextCard[] {
  const q = normKey(query);
  if (!q) return cards;
  return cards.filter((c) => c.norm_keys.includes(q));
}

const wordAscii = (ch: string): boolean => ch.charCodeAt(0) < 128 && /[a-z0-9_]/i.test(ch);

function appearsWithBoundary(nt: string, key: string): boolean {
  for (let i = nt.indexOf(key); i !== -1; i = nt.indexOf(key, i + 1)) {
    const j = i + key.length;
    const leftOk = i === 0 || !(wordAscii(key[0]) && wordAscii(nt[i - 1]));
    const rightOk = j === nt.length || !(wordAscii(key[key.length - 1]) && wordAscii(nt[j]));
    if (leftOk && rightOk) return true;
  }
  return false;
}

/** Free-text scan — the match(text) preview: cards whose key appears in the
 * passage without being glued into a longer ASCII word ("m4" not in "m40";
 * CJK keys match mid-sentence). Empty → all. */
export function scanPassage(passage: string, cards: KbContextCard[]): KbContextCard[] {
  const nt = normKey(passage);
  if (!nt) return cards;
  return cards.filter((c) => c.norm_keys.some((k) => appearsWithBoundary(nt, k)));
}
