/**
 * #455 — derive a work item's write permission on the FE. Mirrors the backend
 * `perm/authorize` decision for a write verb (`src/workspace_app/perm/authorize.py`)
 * for a plain human actor: owner always writes; absent permission ≡ public;
 * public → anyone; private → owner-only; restricted → whoever is granted a write
 * verb (`user:<id>` or `all`). It's a UX gate — the server still enforces — so the
 * read-only user's write affordances (inline edit / +New / drag) are hidden.
 */

export type ItemVisibility = "public" | "restricted" | "private";

/** #306 PR3 — the item access verbs the sharing UI reasons about (grill D1+D3),
 * a superset of the collection ones: read_meta (see it exists) / read_chat (enter
 * the workspace) / read_content (see files) / converse (drive the agent) plus the
 * write side (add/edit_content, execute). change_permission / use_terminal are not
 * offered in the UI (owner-only / human-terminal). */
export type ItemVerb =
  | "read_meta"
  | "read_chat"
  | "read_content"
  | "converse"
  | "add_content"
  | "edit_content"
  | "execute";

export type ItemRoleId =
  | "discoverable"
  | "in_workspace"
  | "reader"
  | "participant"
  | "collaborator";

export type ItemRoleDef = { id: ItemRoleId; label: string; hint: string; verbs: ItemVerb[] };

/** The item role ladder, LOW → HIGH (grill D1+D3). Each role bundles the verbs
 * below it (nested), matching the four tiers the user named + a write-side
 * Collaborator. The permission dialog offers these as a dropdown; a non-nested
 * combination ("read chat but not files") is expressed via Custom per-verb. */
export const ITEM_ROLES: ItemRoleDef[] = [
  { id: "discoverable", label: "Discoverable", hint: "Sees it exists + can request access", verbs: ["read_meta"] },
  { id: "in_workspace", label: "In workspace", hint: "Can enter + watch the conversation", verbs: ["read_meta", "read_chat"] },
  { id: "reader", label: "Reader", hint: "Can also read the files", verbs: ["read_meta", "read_chat", "read_content"] },
  { id: "participant", label: "Participant", hint: "Can also talk to the agent", verbs: ["read_meta", "read_chat", "read_content", "converse"] },
  { id: "collaborator", label: "Collaborator", hint: "Can also edit files + run code", verbs: ["read_meta", "read_chat", "read_content", "converse", "add_content", "edit_content", "execute"] },
];

/** The subset of the wire `Permission` the write gate needs. Other verbs exist
 * on the object but don't affect whether a user may write records. */
export type ItemPermission = {
  visibility: ItemVisibility;
  read_meta?: string[];
  read_chat?: string[];
  read_content?: string[];
  converse?: string[];
  edit_content?: string[];
  add_content?: string[];
  write_meta?: string[];
  execute?: string[];
  // unmanaged by the item UI but preserved verbatim on save
  change_permission?: string[];
  use_terminal?: string[];
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

/** The item's effective visibility for DISPLAY (#578). An absent or malformed
 * `permission` is `public` — that is the backend's rule (`WorkItemBase.permission`:
 * absent ≡ public, legacy rows were never migrated), and it is precisely the row
 * an owner scanning for "what have I left open?" must see flagged. Rendering it
 * blank would hide the one case that matters. */
export function itemVisibility(raw: unknown): ItemVisibility {
  return parseItemPermission(raw)?.visibility ?? "public";
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

/** #306 PR3 — mirror `perm/authorize` for ONE item verb, for the FE lock states
 * (hide the thread without read_chat, the files without read_content, disable the
 * composer without converse). Owner always allowed; absent ≡ public → allowed;
 * private → owner-only; restricted → granted (`user:<id>` or `all`). */
export function hasItemVerb(
  permission: ItemPermission | undefined,
  currentUserId: string,
  ownerId: string,
  verb: ItemVerb,
): boolean {
  if (currentUserId === ownerId) return true;
  if (!permission || permission.visibility === "public") return true;
  if (permission.visibility === "private") return false;
  const grants = permission[verb];
  return (
    Array.isArray(grants) && (grants.includes(`user:${currentUserId}`) || grants.includes("all"))
  );
}

/** #306 PR3 — who may open the sharing UI, mirroring `perm/authorize.py` step 5.
 *
 * `change_permission` is deliberately NOT routed through {@link hasItemVerb}: the
 * backend special-cases it so that `public` visibility never confers it — only
 * the owner, a superuser, or an explicit grant may rewire access control. Reusing
 * the generic check would hand the share control to every viewer of a public item.
 *
 * The UI gate exists so the affordance matches what the server will accept; the
 * server enforces regardless. Getting it WRONG in the other direction is what
 * left admins unable to change any item's access: the control was `me === owner`,
 * while the backend had always honoured superusers and delegates. */
export function canChangeItemPermission(
  permission: ItemPermission | undefined,
  currentUserId: string,
  ownerId: string,
  isSuperuser: boolean,
): boolean {
  if (currentUserId === ownerId || isSuperuser) return true;
  const grants = permission?.change_permission;
  return (
    Array.isArray(grants) && (grants.includes(`user:${currentUserId}`) || grants.includes("all"))
  );
}

export const canReadChat = (p: ItemPermission | undefined, u: string, o: string) =>
  hasItemVerb(p, u, o, "read_chat");
export const canReadItemContent = (p: ItemPermission | undefined, u: string, o: string) =>
  hasItemVerb(p, u, o, "read_content");
export const canConverse = (p: ItemPermission | undefined, u: string, o: string) =>
  hasItemVerb(p, u, o, "converse");
/** The disclosure case: sees the item exists (read_meta) but can't enter it
 * (no read_chat) — the 🔒 locked list row that offers "request access". */
export const isDiscoverableOnly = (p: ItemPermission | undefined, u: string, o: string) =>
  hasItemVerb(p, u, o, "read_meta") && !hasItemVerb(p, u, o, "read_chat");

const subjectUser = (s: string): string | null => (s.startsWith("user:") ? s.slice(5) : null);

/** The deepest nested ITEM role a verb set fully satisfies (grill D2); `null` when
 * it doesn't even reach Discoverable (no read_meta), and `"custom"` conceptually
 * when a non-nested combo is held (the dialog then shows Custom). */
export function itemRoleForVerbs(verbs: Set<string>): ItemRoleId | null {
  for (let i = ITEM_ROLES.length - 1; i >= 0; i--) {
    if (ITEM_ROLES[i].verbs.every((v) => verbs.has(v))) return ITEM_ROLES[i].id;
  }
  return null;
}

export const itemRoleDef = (id: ItemRoleId): ItemRoleDef =>
  ITEM_ROLES.find((r) => r.id === id) ?? ITEM_ROLES[0];

export type ItemGrant = { userId: string; role: ItemRoleId; verbs: Set<string> };

/** The union of verbs any item role touches — the set the dialog OWNS; everything
 * else on the permission (change_permission, use_terminal, write_meta) is preserved
 * verbatim on save. */
export const ITEM_ROLE_VERBS: ItemVerb[] = Array.from(
  new Set(ITEM_ROLES.flatMap((r) => r.verbs)),
) as ItemVerb[];

/** Decode a permission's USER grants into per-user (role | custom verbs) rows for
 * the item share dialog. Group / `all` subjects are ignored (preserved on save);
 * the owner is dropped (they hold everything). `verbs` carries the raw per-user
 * verb set so the Custom mode can render exact toggles for a non-nested combo. */
export function itemGrantsFromPermission(perm: ItemPermission, owner: string): ItemGrant[] {
  const byUser = new Map<string, Set<string>>();
  for (const verb of ITEM_ROLE_VERBS) {
    for (const subject of perm[verb] ?? []) {
      const uid = subjectUser(subject);
      if (uid === null || uid === owner) continue;
      const set = byUser.get(uid) ?? new Set<string>();
      set.add(verb);
      byUser.set(uid, set);
    }
  }
  const grants: ItemGrant[] = [];
  for (const [userId, verbs] of byUser) {
    if (verbs.size === 0) continue;
    const role = itemRoleForVerbs(verbs);
    // `verbs` is left EMPTY when the grant EXACTLY matches a ladder role (the
    // dialog then shows that role); a non-nested combo keeps its raw verb set so
    // the dialog shows Custom with those exact toggles.
    const roleVerbs = role ? itemRoleDef(role).verbs : [];
    const exact = role !== null && roleVerbs.length === verbs.size && roleVerbs.every((v) => verbs.has(v));
    grants.push({ userId, role: role ?? "discoverable", verbs: exact ? new Set<string>() : verbs });
  }
  return grants.sort((a, b) => a.userId.localeCompare(b.userId));
}

/** Encode the dialog's per-user grants back into a full permission (PUT = replace),
 * STARTING from `original` so unmanaged verbs + every non-user subject (groups,
 * `all`) survive. A grant with an explicit `verbs` set (Custom) writes exactly
 * those; otherwise the role's verb bundle is used. */
export function itemPermissionFromGrants(
  visibility: ItemVisibility,
  grants: ItemGrant[],
  original: ItemPermission,
): ItemPermission {
  const next: ItemPermission = { ...original, visibility };
  for (const verb of ITEM_ROLE_VERBS) {
    const kept = (original[verb] ?? []).filter((s) => subjectUser(s) === null);
    const users = grants
      .filter((g) => (g.verbs.size > 0 ? g.verbs.has(verb) : itemRoleDef(g.role).verbs.includes(verb)))
      .map((g) => `user:${g.userId}`);
    next[verb] = Array.from(new Set([...kept, ...users]));
  }
  return next;
}
