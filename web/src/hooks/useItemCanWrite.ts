/**
 * #455 §E — resolve whether the signed-in user may WRITE a work item's entity
 * records, so the renderers can hide/disable every write affordance for a
 * read-only member. Composes the item's `permission` + `created_by` (from the
 * App resource) with the current user through the pure `canWriteItem` decision,
 * which mirrors the backend `perm/authorize` for a human write verb.
 *
 * It's a UX gate only — the server still enforces on every write — so while the
 * item is still loading it stays optimistically writable (no flash of a
 * read-only board before the permission is known).
 */

import { useItemAccess } from "./useItemAccess";
import { useAppItem, useAppManifest } from "./useResources";

export function useItemCanWrite(slug: string, itemId: string): boolean {
  const manifest = useAppManifest(slug);
  const item = useAppItem(slug, manifest?.resource_route, itemId);
  const access = useItemAccess(item);
  if (!item) return true; // optimistic while the item / manifest is still loading
  return access.canWrite;
}
