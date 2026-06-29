/** Per-message "max KB searches" pick for the chat composer (#334).
 *
 * An integer cap on how many times ONE reply may run kb_search:
 *   - app chat (RCA/topic-hub): one budget shared across the turn's
 *     ask_knowledge_base calls (Q6);
 *   - KB chat: this reply's kb_search budget.
 * 0 = don't search this reply (answer from context only, Q4).
 *
 * Sticky in localStorage like the depth picker, so the composer survives a
 * reload. Sent verbatim as `body.max_kb_searches` (the BE re-clamps to the
 * operator ceiling, so an over-large value is safe). The FE stepper bounds it
 * to [0, KB_SEARCH_MAX_UI_MAX]; the value is ALWAYS sent (default 3) — the BE's
 * operator default only applies when the field is omitted by an API caller.
 */
import { useCallback, useState } from "react";

export const KB_SEARCH_MAX_DEFAULT = 3;
/** FE stepper upper bound. The BE clamps to the operator's configured ceiling. */
export const KB_SEARCH_MAX_UI_MAX = 10;

const KEY = "rca.kbSearchMax";

export function clampKbSearchMax(n: number): number {
  if (!Number.isFinite(n)) return KB_SEARCH_MAX_DEFAULT;
  return Math.max(0, Math.min(KB_SEARCH_MAX_UI_MAX, Math.floor(n)));
}

export function getKbSearchMax(): number {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw === null) return KB_SEARCH_MAX_DEFAULT;
    const n = Number(raw);
    return Number.isNaN(n) ? KB_SEARCH_MAX_DEFAULT : clampKbSearchMax(n);
  } catch {
    return KB_SEARCH_MAX_DEFAULT;
  }
}

export function setKbSearchMax(n: number): void {
  try {
    localStorage.setItem(KEY, String(clampKbSearchMax(n)));
  } catch {
    /* localStorage unavailable (private mode / SSR) — the pick just isn't sticky */
  }
}

/** React state bound to the sticky max-searches pick. */
export function useKbSearchMax(): readonly [number, (n: number) => void] {
  const [n, setN] = useState(getKbSearchMax);
  const set = useCallback((v: number) => {
    const c = clampKbSearchMax(v);
    setN(c);
    setKbSearchMax(c);
  }, []);
  return [n, set] as const;
}
