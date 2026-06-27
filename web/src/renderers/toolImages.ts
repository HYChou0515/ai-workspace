/**
 * Extract image paths a tool wrote, from its result text (#285 inline charts).
 *
 * Plotting tools (sci-plot, csv-column-summary) print a JSON result naming the
 * PNGs they wrote — `{"images": [...]}` (sci-plot) or `{"plots": [...]}`
 * (csv-column-summary) — embedded in the tool card's output text. We pull those
 * paths out so the chat can render the charts inline instead of leaving them
 * buried in the file browser. Purely text-driven (no new event/schema), so it
 * works the same on the live SSE output and on a reloaded thread, and any future
 * tool that emits the same shape benefits for free.
 */

const IMAGE_EXT = /\.(png|jpe?g|svg|webp|gif|bmp)$/i;
const KEYS = ["images", "plots"];

export function extractToolImages(text: string | undefined | null): string[] {
  if (!text) return [];
  const out: string[] = [];
  for (const key of KEYS) {
    // The result JSON is pretty-printed, so the array spans newlines — a negated
    // class `[^\]]*` matches those (no `s` flag needed) up to the closing `]`.
    const m = text.match(new RegExp(`"${key}"\\s*:\\s*(\\[[^\\]]*\\])`));
    if (!m) continue;
    try {
      const arr: unknown = JSON.parse(m[1]);
      if (Array.isArray(arr)) {
        for (const p of arr) {
          if (typeof p === "string" && IMAGE_EXT.test(p)) out.push(p);
        }
      }
    } catch {
      // Not valid JSON (truncated mid-stream, or a coincidental key) — skip.
    }
  }
  return [...new Set(out)];
}
