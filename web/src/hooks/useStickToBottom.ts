/**
 * Keep a scroll container pinned to the bottom as content grows — unless
 * the user has scrolled up, in which case we leave them alone until they
 * scroll back to the bottom, then resume auto-scrolling. Used by the chat,
 * agent log and run-history panes.
 */

import { useEffect, useLayoutEffect, useRef } from "react";

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
    const sync = () => {
      stuck.current = isNearBottom(el.scrollTop, el.clientHeight, el.scrollHeight);
    };
    // A user scrolling UP must release the pin *immediately*. `scroll` events
    // are async/coalesced, so during fast streaming the next chunk's pin would
    // fire before the scroll handler ran — yanking the user back down and making
    // it impossible to scroll up. wheel/touch are synchronous user-intent
    // signals, so we drop the pin on the spot; `scroll` then re-sticks once the
    // user returns to the bottom (also covers scrollbar drag + keyboard).
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) stuck.current = false;
    };
    el.addEventListener("scroll", sync, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchmove", sync, { passive: true });
    return () => {
      el.removeEventListener("scroll", sync);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchmove", sync);
    };
  }, []);

  // Pin before paint (layout effect) so following output doesn't flash.
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [dep]);

  return ref;
}
