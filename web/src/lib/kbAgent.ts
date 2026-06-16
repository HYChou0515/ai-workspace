/** Sticky-localStorage picker for the chosen KB-chat model (issue #32).
 * `null` means "use the BE's default" (the first kb_chats[] entry).
 * Same shape as lib/reasoningEffort + lib/kbEnhancementMode so the FE
 * composer has a consistent feel across its pickers.
 */
import { useCallback, useState } from "react";

const KEY = "rca.kbAgentName";

export function getKbAgentName(): string | null {
  try {
    const v = localStorage.getItem(KEY);
    return v && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

export function setKbAgentName(value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, value);
  } catch {
    /* localStorage unavailable — picker just isn't sticky */
  }
}

/** React state bound to the sticky value. */
export function useKbAgentName(): [string | null, (v: string | null) => void] {
  const [value, setValue] = useState<string | null>(getKbAgentName);
  const set = useCallback((v: string | null) => {
    setKbAgentName(v);
    setValue(v);
  }, []);
  return [value, set];
}
