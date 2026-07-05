/**
 * #455 — derive a work item's write permission on the FE. Mirrors the backend
 * `perm/authorize` decision for a write verb (`src/workspace_app/perm/authorize.py`)
 * for a plain human actor: owner always writes; absent permission ≡ public;
 * public → anyone; private → owner-only; restricted → whoever is granted a write
 * verb (`user:<id>` or `all`). It's a UX gate — the server still enforces — so the
 * read-only user's write affordances (inline edit / +New / drag) are hidden.
 */

export type ItemVisibility = "public" | "restricted" | "private";

/** The subset of the wire `Permission` the write gate needs. Other verbs exist
 * on the object but don't affect whether a user may write records. */
export type ItemPermission = {
  visibility: ItemVisibility;
  edit_content?: string[];
  add_content?: string[];
  write_meta?: string[];
};

const WRITE_VERBS = ["edit_content", "add_content", "write_meta"] as const;

/** Narrow the item's opaque `permission` field (it rides through `getAppItem` as
 * `unknown`) into an `ItemPermission`, or `undefined` when absent/malformed —
 * which `canWriteItem` treats as public (the backend's absent ≡ public). */
export function parseItemPermission(raw: unknown): ItemPermission | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const visibility = (raw as Record<string, unknown>).visibility;
  if (visibility !== "public" && visibility !== "restricted" && visibility !== "private") return undefined;
  return raw as ItemPermission;
}

export function canWriteItem(
  permission: ItemPermission | undefined,
  currentUserId: string,
  ownerId: string,
): boolean {
  if (currentUserId === ownerId) return true; // the owner controls their resource
  if (!permission || permission.visibility === "public") return true; // absent ≡ public; public allows the verb
  if (permission.visibility === "private") return false; // non-owner + private
  const me = `user:${currentUserId}`; // restricted → granted any write verb
  return WRITE_VERBS.some((verb) => {
    const grants = permission[verb];
    return Array.isArray(grants) && (grants.includes(me) || grants.includes("all"));
  });
}
