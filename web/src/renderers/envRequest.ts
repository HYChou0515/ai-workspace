/**
 * The card `request_env` declared for the chat to draw
 * (docs/plan-env-request-card.md).
 *
 * The tool's reply is a sentence for the model followed by
 * `\n[env-request]{"tool": …, "names": […], "reason": …}` — a fixed marker, like
 * `[skill-hub-entry]` (`skillHubEntry.ts`), so prose can never trigger a card.
 * Whether each name is a sign-in or a field, and whether it is set yet, is
 * decided when the card is drawn, from the viewer's own values.
 *
 * Mirrors `ENV_REQUEST_MARKER` on the backend — keep them in sync.
 */
const MARKER = "\n[env-request]";

export type EnvRequest = { tool: string; names: string[]; reason: string };

/** The declared request, or `null`. Never throws: tool output streams, so a
 * mid-turn call legitimately sees truncated JSON. */
export function parseEnvRequest(output: string | undefined | null): EnvRequest | null {
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
  const { tool, names, reason } = parsed as Record<string, unknown>;
  if (typeof tool !== "string" || typeof reason !== "string") return null;
  if (!Array.isArray(names) || names.length === 0) return null;
  if (!names.every((n): n is string => typeof n === "string" && n !== "")) return null;
  return { tool, names, reason };
}
