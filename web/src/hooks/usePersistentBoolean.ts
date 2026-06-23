import { useCallback, useState } from "react";

/** A boolean persisted to localStorage (panel visibility toggles etc.).
 *
 * `initial` is only the first-time default — a stored value always wins, so a
 * per-App layout preference (e.g. whether the file IDE is collapsed, #159)
 * survives reloads. The setter accepts EITHER `next: boolean` OR a functional
 * updater `(prev) => next` for toggles. */
export function usePersistentBoolean(
  key: string,
  initial: boolean,
): [boolean, (b: boolean | ((prev: boolean) => boolean)) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw == null) return initial;
      return raw === "true";
    } catch {
      return initial;
    }
  });

  const set = useCallback(
    (b: boolean | ((prev: boolean) => boolean)) => {
      setValue((prev) => {
        const next = typeof b === "function" ? b(prev) : b;
        try {
          localStorage.setItem(key, String(next));
        } catch {
          /* ignore quota / privacy-mode errors */
        }
        return next;
      });
    },
    [key],
  );

  return [value, set];
}
