/** Per-message "max wiki searches" pick for the KB chat composer (#506).
 *
 * An integer cap on how many times ONE reply may run `search_wiki` (grep over the
 * collections' wiki pages). It REPLACES the old boolean "search wiki" toggle: wiki
 * is now a budgeted in-agent tool, symmetric to kb_search, not a routing switch.
 *   - 0 = don't grep the wiki this reply (answer from RAG + what's known);
 *   - N = at most N greps;
 * Sticky in localStorage like the kb-search picker, so the composer survives a
 * reload. Sent verbatim as `body.max_wiki_searches` (the BE re-clamps to the
 * operator ceiling, so an over-large value is safe). The FE stepper bounds it to
 * [0, KB_WIKI_MAX_UI_MAX]; the value is ALWAYS sent (default 3).
 */
import { useCallback, useState } from "react";

export const KB_WIKI_MAX_DEFAULT = 3;
/** FE stepper upper bound. The BE clamps to the operator's configured ceiling. */
export const KB_WIKI_MAX_UI_MAX = 10;

const KEY = "rca.kbWikiMax";

export function clampKbWikiMax(n: number): number {
  if (!Number.isFinite(n)) return KB_WIKI_MAX_DEFAULT;
  return Math.max(0, Math.min(KB_WIKI_MAX_UI_MAX, Math.floor(n)));
}

export function getKbWikiMax(): number {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw === null) return KB_WIKI_MAX_DEFAULT;
    const n = Number(raw);
    return Number.isNaN(n) ? KB_WIKI_MAX_DEFAULT : clampKbWikiMax(n);
  } catch {
    return KB_WIKI_MAX_DEFAULT;
  }
}

export function setKbWikiMax(n: number): void {
  try {
    localStorage.setItem(KEY, String(clampKbWikiMax(n)));
  } catch {
    /* localStorage unavailable (private mode / SSR) — the pick just isn't sticky */
  }
}

/** React state bound to the sticky max-wiki-searches pick. */
export function useKbWikiMax(): readonly [number, (n: number) => void] {
  const [n, setN] = useState(getKbWikiMax);
  const set = useCallback((v: number) => {
    const c = clampKbWikiMax(v);
    setN(c);
    setKbWikiMax(c);
  }, []);
  return [n, set] as const;
}
