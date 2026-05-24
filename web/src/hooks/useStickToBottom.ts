/**
 * Keep a scroll container pinned to the bottom as content grows — unless
 * the user has scrolled up, in which case we leave them alone until they
 * scroll back to the bottom, then resume auto-scrolling. Used by the chat,
 * agent log and run-history panes.
 */

import { useEffect, useRef } from "react";

/** True when the scroll position is within `threshold` px of the bottom. */
export function isNearBottom(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  threshold = 24,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}

/** Attach the returned ref to the scroll container. Pass a value that
 * changes whenever content is appended (e.g. entry count) as `dep`. */
export function useStickToBottom<T extends HTMLElement>(dep: unknown) {
  const ref = useRef<T>(null);
  const stuck = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      stuck.current = isNearBottom(el.scrollTop, el.clientHeight, el.scrollHeight);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [dep]);

  return ref;
}
