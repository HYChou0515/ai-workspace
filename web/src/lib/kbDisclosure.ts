/** Per-chat "disclose withheld sources" toggle (#605).
 *
 * ON (default): every reply runs the scores-only disclosure probe, so the chat
 * can say "an answer exists in a collection you can't read" (with request-
 * access). OFF: the probe is skipped — one fewer vector query per search, no
 * withheld sources this chat. OFF can never re-enable a deploy whose operator
 * switched `kb.disclosure.enabled` off; the BE ANDs both.
 *
 * Sticky in localStorage like the depth picker / search-max steppers, and sent
 * on every message as `body.disclosure` by both chat surfaces (KB chat + app
 * chat's ask_knowledge_base path).
 */
import { useCallback, useState } from "react";

const KEY = "rca.kbDisclosure";

export function getKbDisclosure(): boolean {
  try {
    // Anything other than the explicit opt-out reads as the default (on).
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setKbDisclosure(on: boolean): void {
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "off");
  } catch {
    /* localStorage unavailable — the pick just isn't sticky */
  }
}

/** React state bound to the sticky disclosure toggle. */
export function useKbDisclosure(): readonly [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(getKbDisclosure);
  const set = useCallback((v: boolean) => {
    setOn(v);
    setKbDisclosure(v);
  }, []);
  return [on, set] as const;
}
