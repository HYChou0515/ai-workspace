import { useCallback, useState } from "react";

/** A number persisted to localStorage (panel sizes etc.). Clamps to
 * [min, max] on set so a stored bad value can't wedge the layout.
 *
 * The setter accepts EITHER `next: number` OR a functional updater
 * `(prev) => next`. Use the updater form whenever the new value depends on
 * the previous one and the call may fire many times per frame (e.g. drag
 * handlers) — closures captured at render time go stale faster than React
 * can re-render, and only the last delta would "win". */
export function usePersistentNumber(
  key: string,
  initial: number,
  min = 0,
  max = Number.POSITIVE_INFINITY,
): [number, (n: number | ((prev: number) => number)) => void] {
  const [value, setValue] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw == null) return initial;
      const n = Number.parseFloat(raw);
      return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : initial;
    } catch {
      return initial;
    }
  });

  const set = useCallback(
    (n: number | ((prev: number) => number)) => {
      setValue((prev) => {
        const next = typeof n === "function" ? n(prev) : n;
        const clamped = Math.min(max, Math.max(min, next));
        try {
          localStorage.setItem(key, String(clamped));
        } catch {
          /* ignore quota / privacy-mode errors */
        }
        return clamped;
      });
    },
    [key, min, max],
  );

  return [value, set];
}
