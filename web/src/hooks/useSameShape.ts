import { useRef } from "react";

import { sameShape } from "../lib/sameShape";

/**
 * `value`, or the previous render's value when it has the same shape
 * (`sameShape`) — so a value recomputed on every render keeps its identity
 * until its CONTENT changes, and a `memo`'d child or a context consumer that
 * receives it is not redrawn for nothing.
 *
 * Built for the chat feed: a value derived from `log.entries` is recomputed on
 * every streamed token, while what it says (which questions are answered)
 * changes once in a whole turn.
 */
export function useSameShape<T>(value: T): T {
  const kept = useRef(value);
  if (kept.current !== value && !sameShape(kept.current, value)) kept.current = value;
  return kept.current;
}
