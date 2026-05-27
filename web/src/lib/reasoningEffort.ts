/** The per-message reasoning-effort selection, remembered across reloads in
 * localStorage (sticky UI, client-side). `null` = Default (don't send the
 * param → the model's own default). */
import { useCallback, useState } from "react";

import type { ReasoningEffort } from "../api/types";

const KEY = "rca.reasoningEffort";
const VALID: ReasoningEffort[] = ["low", "medium", "high"];

export function getReasoningEffort(): ReasoningEffort | null {
  try {
    const v = localStorage.getItem(KEY);
    return v && (VALID as string[]).includes(v) ? (v as ReasoningEffort) : null;
  } catch {
    return null;
  }
}

export function setReasoningEffort(value: ReasoningEffort | null): void {
  try {
    if (value === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, value);
  } catch {
    /* localStorage unavailable (private mode / SSR) — selection just isn't sticky */
  }
}

/** React state bound to the sticky value. */
export function useReasoningEffort(): [ReasoningEffort | null, (v: ReasoningEffort | null) => void] {
  const [value, setValue] = useState<ReasoningEffort | null>(getReasoningEffort);
  const set = useCallback((v: ReasoningEffort | null) => {
    setReasoningEffort(v);
    setValue(v);
  }, []);
  return [value, set];
}
