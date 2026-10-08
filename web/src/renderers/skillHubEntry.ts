/**
 * The skill hub entry `show_skill_hub_entry` declared for the chat to draw.
 *
 * The tool's result is a sentence for the model followed by
 * `\n[skill-hub-entry]{"entry_id": "…"}` — a fixed marker, like
 * `[shown-files]` (`shownFiles.ts`), so prose can never trigger a card. The
 * card reads the entry live from the id; nothing else travels.
 *
 * Mirrors `SKILL_HUB_ENTRY_MARKER` on the backend — keep them in sync.
 */
const MARKER = "\n[skill-hub-entry]";

/** The output without the declaration — the sentence a person reads. A
 * marker still arriving (a trailing prefix of it) goes too. */
export function stripSkillHubEntry(output: string | undefined): string | undefined {
  if (!output) return output;
  const at = output.lastIndexOf(MARKER);
  if (at >= 0) return output.slice(0, at);
  for (let n = MARKER.length - 1; n > 0; n--) {
    if (output.endsWith(MARKER.slice(0, n))) return output.slice(0, output.length - n);
  }
  return output;
}

/** The declared entry id, or `null`. Never throws: tool output streams, so a
 * mid-turn call legitimately sees truncated JSON. */
export function parseShownSkillHubEntry(output: string | undefined | null): string | null {
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
  const id = (parsed as { entry_id?: unknown }).entry_id;
  return typeof id === "string" && id !== "" ? id : null;
}
