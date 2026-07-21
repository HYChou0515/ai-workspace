/**
 * The single place the UI asks "what may the signed-in user do with THIS item?".
 *
 * Every per-verb decision mirrors the backend `perm/authorize` (see
 * `lib/itemPermission.ts`), which needs TWO facts about the actor: their user id
 * AND whether they are a superuser. Components used to compose only the first,
 * calling `canReadItemContent(perm, useCurrentUser(), item.created_by)` — so an
 * admin, who the backend and the item list scope both let through, hit the
 * `visibility === "private"` branch and got an empty workspace: no file tree, a
 * read-only composer, and no message saying why. Bundling both identity bits here
 * makes "forgot the superuser half" unrepresentable at the call site.
 *
 * It is a UX gate only — the server still enforces on every route.
 */

import type { AppItem } from "../api/types";
import {
  canConverse,
  canReadChat,
  canReadItemContent,
  canWriteItem,
  isDiscoverableOnly,
  parseItemPermission,
} from "../lib/itemPermission";
import { useCurrentUser } from "./useCurrentUser";
import { useIsSuperuser } from "./useIsSuperuser";

export type ItemAccess = {
  /** Enter the item and watch its conversation (`read_chat`). */
  canReadChat: boolean;
  /** See the file tree / editor (`read_content`). */
  canSeeFiles: boolean;
  /** Drive the agent — the composer is read-only without it (`converse`). */
  canConverse: boolean;
  /** Write entity records / edit files (`edit_content` family). */
  canWrite: boolean;
  /** Sees it exists but cannot enter — the 🔒 list row offering "request access". */
  isDiscoverableOnly: boolean;
};

export function useItemAccess(item: AppItem | undefined): ItemAccess {
  const me = useCurrentUser();
  const isSuperuser = useIsSuperuser();
  // Owner-for-access is `created_by` (the real owner), not the display `owner`
  // field, which apps are free to repurpose as a domain assignee.
  const owner = item?.created_by ?? "";
  const perm = parseItemPermission(item?.permission);
  return {
    canReadChat: canReadChat(perm, me, owner, isSuperuser),
    canSeeFiles: canReadItemContent(perm, me, owner, isSuperuser),
    canConverse: canConverse(perm, me, owner, isSuperuser),
    canWrite: canWriteItem(perm, me, owner, isSuperuser),
    isDiscoverableOnly: isDiscoverableOnly(perm, me, owner, isSuperuser),
  };
}
