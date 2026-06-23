/** The per-message reasoning-effort selection, remembered across reloads in
 * localStorage (sticky UI, client-side). #160 removed the "Auto" (don't-send)
 * option: the dial is always low/medium/high and defaults to the lightest. */
import { useCallback, useState } from "react";

import type { ReasoningEffort } from "../api/types";

const KEY = "rca.reasoningEffort";
const VALID: ReasoningEffort[] = ["low", "medium", "high"];
const DEFAULT: ReasoningEffort = "low";

export function getReasoningEffort(): ReasoningEffort {
  try {
    const v = localStorage.getItem(KEY);
    return v && (VALID as string[]).includes(v) ? (v as ReasoningEffort) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function setReasoningEffort(value: ReasoningEffort): void {
  try {
    localStorage.setItem(KEY, value);
  } catch {
    /* localStorage unavailable (private mode / SSR) — selection just isn't sticky */
  }
}

/** React state bound to the sticky value. */
export function useReasoningEffort(): [ReasoningEffort, (v: ReasoningEffort) => void] {
  const [value, setValue] = useState<ReasoningEffort>(getReasoningEffort);
  const set = useCallback((v: ReasoningEffort) => {
    setReasoningEffort(v);
    setValue(v);
  }, []);
  return [value, set];
}
