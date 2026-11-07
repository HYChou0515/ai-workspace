import { useMemo } from "react";

import { itemPeople } from "../lib/itemPermission";
import { useAppItem, useAppManifest } from "./useResources";

/** The people who belong to an item (`itemPeople`), looked up from its address —
 * for a people picker in a place that is about that item but holds only its id
 * (docs/plan-people-picker-ranking.md D4). Empty until the item has loaded; both
 * reads are cached queries the workspace has usually made already. */
export function useItemPeople(slug: string, itemId: string): string[] {
  const manifest = useAppManifest(slug);
  const item = useAppItem(slug, manifest?.resource_route, itemId);
  return useMemo(() => (item ? itemPeople(item) : []), [item]);
}
