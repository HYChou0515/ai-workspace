/**
 * Names an item's access in one word: Public / Restricted / Private (#578).
 *
 * An owner scanning the item table needs to spot what is already open without
 * opening a share dialog per row. All three states are shown, never just the
 * "interesting" ones — an absent chip would be ambiguous between "public" and
 * "not loaded".
 */

import { chipStyle, type ChipTone } from "./StatusChip";
import type { ItemVisibility } from "../lib/itemPermission";

// `private` is the quiet default for a new item, so it is the muted one; the
// tones rank by how far the item reaches, not by how alarming it is — an item
// being public is a fact to notice, not an error to fix.
const TONE: Record<ItemVisibility, ChipTone> = {
  public: "info",
  restricted: "warn",
  private: "muted",
};

const LABEL: Record<ItemVisibility, string> = {
  public: "Public",
  restricted: "Restricted",
  private: "Private",
};

const HINT: Record<ItemVisibility, string> = {
  public: "Everyone in the workspace can open this",
  restricted: "Only you and the people you named",
  private: "Only you",
};

export function AccessChip({ visibility }: { visibility: ItemVisibility }) {
  return (
    <span style={chipStyle(TONE[visibility])} title={HINT[visibility]}>
      {LABEL[visibility]}
    </span>
  );
}
