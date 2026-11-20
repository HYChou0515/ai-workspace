/**
 * Which rows of a long chat feed are drawn (docs/plan-chat-collapse-middle.md).
 *
 * A feed draws its first `head` rows and its last `tail` rows; what lies between
 * is collapsed behind one bar. Both are counts, not indices: the first load, a
 * reconnect and an undo each rebuild the whole log, so an index taken when the
 * feed opened would not survive them (D5).
 */

/** Rows drawn from the top by default (D1). */
export const FEED_HEAD = 100;
/** Rows drawn from the bottom by default (D1). */
export const FEED_TAIL = 1000;
/** Rows one press of either expand button reveals (D2). */
export const FEED_STEP = 1000;

export type FeedWindow = {
  /** Rows `[0, headEnd)` are drawn above the bar. */
  headEnd: number;
  /** Rows `[tailStart, total)` are drawn below it. */
  tailStart: number;
  /** Rows behind the bar; 0 means there is no bar. */
  hidden: number;
};

export function feedWindow(total: number, head: number, tail: number): FeedWindow {
  if (head + tail >= total) return { headEnd: total, tailStart: total, hidden: 0 };
  return { headEnd: head, tailStart: total - tail, hidden: total - head - tail };
}
