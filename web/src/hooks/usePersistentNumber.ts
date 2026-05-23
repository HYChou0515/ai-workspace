import { useCallback, useState } from "react";

/** A number persisted to localStorage (panel sizes etc.). Clamps to
 * [min, max] on set so a stored bad value can't wedge the layout. */
export function usePersistentNumber(
  key: string,
  initial: number,
  min = 0,
  max = Number.POSITIVE_INFINITY,
): [number, (n: number) => void] {
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
    (n: number) => {
      const clamped = Math.min(max, Math.max(min, n));
      setValue(clamped);
      try {
        localStorage.setItem(key, String(clamped));
      } catch {
        /* ignore quota / privacy-mode errors */
      }
    },
    [key, min, max],
  );

  return [value, set];
}
