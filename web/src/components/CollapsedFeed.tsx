/**
 * A long chat feed with its middle collapsed (docs/plan-chat-collapse-middle.md).
 *
 * Draws the first `FEED_HEAD` and the last `FEED_TAIL` rows; between them, one
 * bar with an expand button at each end — the GitHub PR-timeline / diff shape:
 * "↓" continues the top part downward, "↑" continues the bottom part upward,
 * `FEED_STEP` rows a press. Both chat panels draw through it; it decides only
 * WHICH rows reach the DOM — `renderRow` still gets each row's index in the
 * whole feed, so whatever finds a row by index (answer, undo, report) is
 * unchanged.
 */

import { type ReactNode, type RefObject, useLayoutEffect, useRef, useState } from "react";

import { FEED_HEAD, FEED_STEP, FEED_TAIL, feedWindow } from "../lib/collapsedFeed";
import { pxToRem } from "../lib/pxToRem";
import { Btn } from "./Btn";

type Shown = { key: string; head: number; tail: number };

/** Where the scroll stood before an expand, to put the reader back (D4). */
type Anchor = { scrollTop: number; scrollHeight: number; grewAbove: boolean };

export function CollapsedFeed<T>({
  entries,
  resetKey,
  scrollRef,
  renderRow,
}: {
  entries: readonly T[];
  /** The chat this feed shows: another one starts over at the defaults (D6). */
  resetKey: string;
  /** The element that scrolls the feed. */
  scrollRef: RefObject<HTMLElement | null>;
  renderRow: (entry: T, index: number) => ReactNode;
}) {
  const [shown, setShown] = useState<Shown>({ key: resetKey, head: FEED_HEAD, tail: FEED_TAIL });
  // Another chat: back to the defaults, in this same render (React's
  // "adjusting state when a prop changes"), so the long one never draws whole.
  const current =
    shown.key === resetKey ? shown : { key: resetKey, head: FEED_HEAD, tail: FEED_TAIL };
  if (current !== shown) setShown(current);

  const anchor = useRef<Anchor | null>(null);
  const win = feedWindow(entries.length, current.head, current.tail);
  const step = Math.min(FEED_STEP, win.hidden);

  const expand = (side: "head" | "tail") => {
    const el = scrollRef.current;
    if (el) {
      anchor.current = {
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        // ↑ adds rows above the bottom part being read; ↓ adds them below the
        // top part, so nothing being read moves.
        grewAbove: side === "tail",
      };
    }
    setShown({ ...current, [side]: current[side] + step });
  };

  // Before paint, so the reader never sees the jump. Restores an absolute
  // position rather than trusting the browser's scroll anchoring, which may
  // pick the bar itself as its anchor and carry the reader past the new rows.
  useLayoutEffect(() => {
    const a = anchor.current;
    const el = scrollRef.current;
    if (!a || !el) return;
    anchor.current = null;
    el.scrollTop = a.grewAbove ? a.scrollTop + (el.scrollHeight - a.scrollHeight) : a.scrollTop;
  });

  return (
    <>
      {entries.slice(0, win.headEnd).map((e, i) => renderRow(e, i))}
      {win.hidden > 0 && (
        <div
          role="group"
          aria-label="收起的訊息"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 6,
            padding: "10px 0",
            borderTop: "1px dashed var(--text-paper-d)",
            borderBottom: "1px dashed var(--text-paper-d)",
            color: "var(--text-paper-d)",
            fontSize: pxToRem(13),
          }}
        >
          <Btn size="sm" onClick={() => expand("head")}>
            ↓ 再顯示接下來的 {step} 則
          </Btn>
          <span>中間還有 {win.hidden} 則未顯示</span>
          <Btn size="sm" onClick={() => expand("tail")}>
            ↑ 再顯示更早的 {step} 則
          </Btn>
        </div>
      )}
      {entries.slice(win.tailStart).map((e, j) => renderRow(e, win.tailStart + j))}
    </>
  );
}
