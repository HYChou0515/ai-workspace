/**
 * The "請幫我查" card `ask_outside` declared for the chat to draw
 * (docs/plan-outside-lookup.md).
 *
 * The tool's reply is a sentence for the model followed by
 * `\n[outside-lookup]{"why": …, "query": …}` or `{"why": …, "url": …}` — a
 * fixed marker, like `[env-request]` (`envRequest.ts`), so prose can never
 * trigger a card.
 *
 * Mirrors `OUTSIDE_LOOKUP_MARKER` / `declared_lookup` on the backend, held to
 * it by `web/tests/outsideLookupParity.test.ts`.
 */
const MARKER = "\n[outside-lookup]";

/** http(s), a host, no whitespace anywhere — the same pattern as the backend's
 * `_WEB_ADDRESS`, not the `URL` parser: two URL parsers disagree on what has a
 * host, and the turn stops on the backend's answer. */
const WEB_ADDRESS = /^https?:\/\/[^/?#\s]+(?:[/?#]\S*)?$/;

export type OutsideLookup =
  | { why: string; query: string }
  | { why: string; url: string };

export function isWebAddress(url: string): boolean {
  return WEB_ADDRESS.test(url);
}

/** The declared card, or `null`. Never throws: tool output streams, so a
 * mid-turn call legitimately sees truncated JSON. */
export function parseOutsideLookup(output: string | undefined | null): OutsideLookup | null {
  if (!output) return null;
  const at = output.lastIndexOf(MARKER);
  if (at < 0) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(output.slice(at + MARKER.length));
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const card = parsed as Record<string, unknown>;
  const keys = Object.keys(card).sort().join(",");
  if (keys !== "query,why" && keys !== "url,why") return null;
  if (!Object.values(card).every((v) => typeof v === "string" && v !== "")) return null;
  if (typeof card.url === "string" && !isWebAddress(card.url)) return null;
  return card as OutsideLookup;
}
