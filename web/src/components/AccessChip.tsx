/**
 * Names an item's access in one word: Public / Restricted / Private (#578).
 *
 * An owner scanning the item table needs to spot what is already open without
 * opening a share dialog per row. All three states are shown, never just the
 * "interesting" ones — an absent chip would be ambiguous between "public" and
 * "not loaded", which defeats scanning.
 *
 * Copy comes from the shared table in `lib/itemPermission` so the chip and the
 * share dialog opened from that same row cannot describe the item differently.
 */

import {
  ITEM_VISIBILITY_HINT,
  ITEM_VISIBILITY_LABEL,
  type DisplayVisibility,
} from "../lib/itemPermission";
import { chipStyle, type ChipTone } from "./StatusChip";

// Salience tracks REACH, and the loudest tone goes to the most-open state: the
// question this column answers is "what have I left open?", so `public` must be
// the one that catches the eye. `warn` is this codebase's caution tone
// (severityTone P2, statusTone triaging) — an item being public is a fact worth
// noticing, which is what caution means here, not an error.
const TONE: Record<DisplayVisibility, ChipTone> = {
  public: "warn",
  restricted: "info",
  private: "muted",
  unknown: "muted",
};

export function AccessChip({ visibility }: { visibility: DisplayVisibility }) {
  const known = visibility !== "unknown";
  return (
    <span
      style={{ ...chipStyle(TONE[visibility]), overflow: "hidden", maxWidth: "100%" }}
      title={known ? ITEM_VISIBILITY_HINT[visibility] : "This item's access setting could not be read"}
    >
      {known ? ITEM_VISIBILITY_LABEL[visibility] : "—"}
    </span>
  );
}
