/**
 * A long chat feed with its middle collapsed (docs/plan-chat-collapse-middle.md).
 *
 * Draws the first `FEED_HEAD` and the last `FEED_TAIL` rows; between them, one
 * bar with an expand button at each end — the GitHub PR-timeline / diff shape:
 * "↓" continues the top part downward, "↑" continues the bottom part upward,
 * `FEED_STEP` rows a press; once no more than that is left, one "show the rest"
 * (a direction would change nothing). It decides only WHICH rows reach the DOM
 * — `renderRow` still gets each row's index in the whole feed, so the actions
 * that find a row by index (replay, undo, report) are unchanged.
 */

import { type ReactNode, type RefObject, useLayoutEffect, useRef, useState } from "react";

import { FEED_HEAD, FEED_STEP, FEED_TAIL, feedWindow } from "../lib/collapsedFeed";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Btn } from "./Btn";

/** `all`: everything was shown once, so it stays shown — a new message must not
 * bring the bar back into a feed the reader opened whole. */
type Shown = { key: string; head: number; tail: number; all: boolean };

/** Where the scroll stood before an expand, to put the reader back (D4). */
type Anchor = { scrollTop: number; scrollHeight: number; grewAbove: boolean };

const initial = (key: string): Shown => ({ key, head: FEED_HEAD, tail: FEED_TAIL, all: false });

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
  /** One row, keyed by its index — the key is what keeps a row mounted as it
   * moves between the top and the bottom part. */
  renderRow: (entry: T, index: number) => ReactNode;
}) {
  const t = useT();
  const [shown, setShown] = useState<Shown>(() => initial(resetKey));
  // Another chat: back to the defaults, in this same render (React's
  // "adjusting state when a prop changes"), so the long one never draws whole.
  const current = shown.key === resetKey ? shown : initial(resetKey);
  if (current !== shown) setShown(current);

  const anchor = useRef<Anchor | null>(null);
  const win = current.all
    ? feedWindow(entries.length, entries.length, 0)
    : feedWindow(entries.length, current.head, current.tail);
  const step = Math.min(FEED_STEP, win.hidden);

  const expand = (side: "head" | "tail" | "all") => {
    const el = scrollRef.current;
    if (el) {
      anchor.current = {
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        // ↓ inserts its rows ABOVE the bar, below the top part: keeping
        // `scrollTop` keeps the top part where it was. ↑ and "the rest" insert
        // theirs below the bar, above the bottom part — the part a reader who
        // came up from the newest messages is in — so the scroll moves by what
        // was added and the bottom part stays put.
        grewAbove: side !== "head",
      };
    }
    setShown(
      side === "all" ? { ...current, all: true } : { ...current, [side]: current[side] + step },
    );
  };

  // Before paint, so the reader never sees the jump. Restores the position
  // itself rather than leaving it to the browser's scroll anchoring: measured in
  // Chromium, without this an ↑ press pushed the row being read down by the
  // whole height of the rows it added (~129k px for 1000 rows).
  useLayoutEffect(() => {
    const a = anchor.current;
    const el = scrollRef.current;
    if (!a || !el) return;
    anchor.current = null;
    el.scrollTop = a.grewAbove ? a.scrollTop + (el.scrollHeight - a.scrollHeight) : a.scrollTop;
  });

  // ONE keyed array, bar included: React matches keys within an array, so a row
  // crossing from the bottom part to the top part (the feed growing past the
  // threshold, an undo, the last expand) keeps its mount — two arrays remounted
  // ~1000 rows there, re-parsing their markdown and closing opened tool cards.
  const rows: ReactNode[] = entries.slice(0, win.headEnd).map((e, i) => renderRow(e, i));
  if (win.hidden > 0) {
    rows.push(
      <div
        key="collapsed-feed-bar"
        role="group"
        aria-label={t("feed.collapsed.label")}
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
        {win.hidden > FEED_STEP ? (
          <>
            <Btn size="sm" onClick={() => expand("head")}>
              {t("feed.collapsed.next", { n: step })}
            </Btn>
            <span>{t("feed.collapsed.hidden", { n: win.hidden })}</span>
            <Btn size="sm" onClick={() => expand("tail")}>
              {t("feed.collapsed.earlier", { n: step })}
            </Btn>
          </>
        ) : (
          <>
            <span>{t("feed.collapsed.hidden", { n: win.hidden })}</span>
            <Btn size="sm" onClick={() => expand("all")}>
              {t("feed.collapsed.rest", { n: win.hidden })}
            </Btn>
          </>
        )}
      </div>,
    );
  }
  entries.slice(win.tailStart).forEach((e, j) => rows.push(renderRow(e, win.tailStart + j)));
  return <>{rows}</>;
}
