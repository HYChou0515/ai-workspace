/**
 * The export dialog's range, counted from the NEWEST message (decision 5 of
 * plan-chat-video-export: "從新往舊數會比較方便" — the message you want is
 * the one you just read, near the bottom), translated to the server's
 * absolute, half-open, oldest-first `[start, end)` — the ONE range both the
 * text export (`?start&end`) and the video's client-side slice use, so the
 * two can never disagree about which messages "the latest 5" are.
 *
 * `null` means the whole thread: no query parameters, no slice — a file named
 * without a range, as before.
 */

export type RangeChoice =
  | { kind: "all" }
  | { kind: "latest"; n: number }
  /** Newest-first numbers, 1 = the newest, in either order. */
  | { kind: "custom"; from: number; to: number };

export type AbsoluteRange = { start: number; end: number };

/** The number a message shows in the dialog: 1 for the newest, `total` for
 * the oldest. `index` is the server's, oldest-first. */
export function newestNumber(total: number, index: number): number {
  return total - index;
}

/** A newest-first number clamped into the thread. */
function clamp(total: number, n: number): number {
  return Math.min(total, Math.max(1, Math.floor(n)));
}

export function absoluteRange(total: number, choice: RangeChoice): AbsoluteRange | null {
  if (total <= 0 || choice.kind === "all") return null;
  let start: number;
  let end: number;
  if (choice.kind === "latest") {
    start = Math.max(0, total - Math.floor(choice.n));
    end = total;
  } else {
    const a = clamp(total, choice.from);
    const b = clamp(total, choice.to);
    // Newest #k is index total - k; the older of the two starts the range.
    start = total - Math.max(a, b);
    end = total - Math.min(a, b) + 1;
  }
  if (start === 0 && end === total) return null;
  return { start, end };
}
