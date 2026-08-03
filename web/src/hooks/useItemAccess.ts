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
 *
 * LOADING CONTRACT: an `undefined` item (still fetching) has no permission to
 * read, and an absent permission is public to `authorize` — so every verb comes
 * back TRUE. That is deliberate for the write gate (`useItemCanWrite` stays
 * optimistically writable rather than flashing a read-only board), but a consumer
 * that renders CONTENT off these flags must gate on the item being loaded first,
 * or it will paint a full workspace for an item it hasn't seen yet.
 *
 * The IDENTITY half follows the same direction: until both identity queries
 * settle (`useCurrentUserState` / `useIsSuperuserState`, resolved OR failed),
 * the fallbacks ("default-user", not a superuser) describe a nobody, and
 * computing verbs from them locked the owner/admin out of a cold deep-link's
 * first paint (🔒 flash, vanished IDE — and `useEntityWrite` silently dropped
 * writes made inside the window). Every verb is optimistic until identity
 * settles; the server still enforces on every route either way.
 */

import type { AppItem } from "../api/types";
import {
  canConverse,
  canReadChat,
  canReadItemContent,
  canWriteItem,
  canWriteItemMeta,
  isDiscoverableOnly,
  parseItemPermission,
} from "../lib/itemPermission";
import { useCurrentUserState } from "./useCurrentUser";
import { useIsSuperuserState } from "./useIsSuperuser";

export type ItemAccess = {
  /** Enter the item and watch its conversation (`read_chat`). */
  canReadChat: boolean;
  /** See the file tree / editor (`read_content`). */
  canSeeFiles: boolean;
  /** Drive the agent — the composer is read-only without it (`converse`). */
  canConverse: boolean;
  /** Write entity records / edit files (`edit_content` family). */
  canWrite: boolean;
  /** Store a field ON the item — env vars, tool/skill prefs, the details form
   * (`write_meta`). Narrower than {@link canWrite} on purpose: the item PATCH
   * accepts write_meta alone, so an editor who passes canWrite is still refused. */
  canWriteMeta: boolean;
  /** Sees it exists but cannot enter — the 🔒 list row offering "request access". */
  isDiscoverableOnly: boolean;
};

export function useItemAccess(item: AppItem | undefined): ItemAccess {
  const { id: me, ready: meReady } = useCurrentUserState();
  const { isSuperuser, groups, ready: superuserReady } = useIsSuperuserState();
  if (!(meReady && superuserReady)) {
    // LOADING CONTRACT, identity half — see the module docstring.
    return {
      canReadChat: true,
      canSeeFiles: true,
      canConverse: true,
      canWrite: true,
      canWriteMeta: true,
      isDiscoverableOnly: false,
    };
  }
  // Owner-for-access is `created_by` (the real owner), not the display `owner`
  // field, which apps are free to repurpose as a domain assignee.
  const owner = item?.created_by ?? "";
  const perm = parseItemPermission(item?.permission);
  return {
    canReadChat: canReadChat(perm, me, owner, isSuperuser, groups),
    canSeeFiles: canReadItemContent(perm, me, owner, isSuperuser, groups),
    canConverse: canConverse(perm, me, owner, isSuperuser, groups),
    canWrite: canWriteItem(perm, me, owner, isSuperuser, groups),
    canWriteMeta: canWriteItemMeta(perm, me, owner, isSuperuser, groups),
    isDiscoverableOnly: isDiscoverableOnly(perm, me, owner, isSuperuser, groups),
  };
}
