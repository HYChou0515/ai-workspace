/** #310 — the shared permission vocabulary + role⇄grant mapping for the sharing
 * dialog. Mirrors the backend `perm` model (docs/plan-permissions.md): a resource
 * carries a `visibility` (public / restricted / private) plus per-verb grant lists
 * of subject tokens. The dialog works in ROLES (a named bundle of verbs) for
 * legibility and expands to the raw verbs for power users; this module is the pure
 * translation both directions, with no React/network in it so it's trivially
 * testable. */

export type Visibility = "public" | "restricted" | "private";

/** The verbs the collection sharing UI reasons about. The wire model has more
 * (read_chat / converse / execute / use_terminal / change_permission); those are
 * NOT managed by a collection's roles and are preserved verbatim on save. */
export type CollectionVerb =
  | "read_meta"
  | "read_content"
  | "add_content"
  | "edit_content"
  | "write_meta";

/** The full per-verb permission object as it crosses the wire (PUT = replace). */
export type CollectionPermission = {
  visibility: Visibility;
  read_meta: string[];
  write_meta: string[];
  read_content: string[];
  add_content: string[];
  edit_content: string[];
  read_chat: string[];
  converse: string[];
  execute: string[];
  use_terminal: string[];
  change_permission: string[];
};

export const ALL_VERBS: (keyof Omit<CollectionPermission, "visibility">)[] = [
  "read_meta",
  "write_meta",
  "read_content",
  "add_content",
  "edit_content",
  "read_chat",
  "converse",
  "execute",
  "use_terminal",
  "change_permission",
];

export type RoleId = "viewer" | "collaborator" | "editor";

export type RoleDef = {
  id: RoleId;
  /** i18n-key-free label; the dialog localises via its own copy. */
  label: string;
  hint: string;
  /** The verbs this role grants. Ordered low→high privilege in COLLECTION_ROLES,
   * which `roleForVerbs` relies on to pick the highest matching tier. */
  verbs: CollectionVerb[];
};

/** The three collection roles, LOW → HIGH privilege. A grantee is rendered as the
 * highest role whose signature verb they hold; saving writes the role's exact
 * verb set. */
export const COLLECTION_ROLES: RoleDef[] = [
  { id: "viewer", label: "Viewer", hint: "Can read documents", verbs: ["read_meta", "read_content"] },
  {
    id: "collaborator",
    label: "Collaborator",
    hint: "Can read + add documents",
    verbs: ["read_meta", "read_content", "add_content"],
  },
  {
    id: "editor",
    label: "Editor",
    hint: "Can read, add + edit documents",
    verbs: ["read_meta", "read_content", "add_content", "edit_content", "write_meta"],
  },
];

/** The verbs any collection role touches — the set the dialog OWNS. Every other
 * verb on the permission is left untouched on save. */
const ROLE_VERBS: Set<string> = new Set(COLLECTION_ROLES.flatMap((r) => r.verbs));

const USER_PREFIX = "user:";

export const userSubject = (id: string): string => `${USER_PREFIX}${id}`;

/** The user id inside a `user:<id>` subject, or null for a group / `all` subject. */
export const subjectUser = (subject: string): string | null =>
  subject.startsWith(USER_PREFIX) ? subject.slice(USER_PREFIX.length) : null;

export const roleDef = (id: RoleId): RoleDef =>
  COLLECTION_ROLES.find((r) => r.id === id) ?? COLLECTION_ROLES[0];

/** The highest role a set of granted verbs satisfies (by signature verb), so a
 * grant made outside the dialog (or via raw verbs) still maps to a legible role.
 * `null` when the verbs don't even reach Viewer (no read grant). */
export function roleForVerbs(verbs: Set<string>): RoleId | null {
  if (verbs.has("edit_content") || verbs.has("write_meta")) return "editor";
  if (verbs.has("add_content")) return "collaborator";
  if (verbs.has("read_content") || verbs.has("read_meta")) return "viewer";
  return null;
}

export type Grant = { userId: string; role: RoleId };

/** Decode a permission's USER grants into (user, role) rows for the dialog. Group
 * / `all` subjects are ignored here (the dialog manages users only; they're
 * preserved on save). A user appears once, at their highest role. `owner` is
 * dropped — the owner isn't a grantee (they hold everything implicitly). */
export function grantsFromPermission(perm: CollectionPermission, owner: string): Grant[] {
  const verbsByUser = new Map<string, Set<string>>();
  for (const verb of ROLE_VERBS) {
    for (const subject of perm[verb as CollectionVerb] ?? []) {
      const uid = subjectUser(subject);
      if (uid === null || uid === owner) continue;
      const set = verbsByUser.get(uid) ?? new Set<string>();
      set.add(verb);
      verbsByUser.set(uid, set);
    }
  }
  const grants: Grant[] = [];
  for (const [userId, verbs] of verbsByUser) {
    const role = roleForVerbs(verbs);
    if (role !== null) grants.push({ userId, role });
  }
  return grants.sort((a, b) => a.userId.localeCompare(b.userId));
}

/** Encode the dialog's state back into a full permission (PUT = replace), STARTING
 * from `original` so everything the dialog doesn't manage survives: unmanaged
 * verbs (read_chat / change_permission / …) verbatim, and — for managed verbs —
 * every non-user subject (group grants from #307, the `all` wildcard). Only the
 * `user:` subjects of role-verbs are rebuilt from `grants`. */
export function permissionFromGrants(
  visibility: Visibility,
  grants: Grant[],
  original: CollectionPermission,
): CollectionPermission {
  const next: CollectionPermission = { ...original, visibility };
  for (const verb of ROLE_VERBS) {
    const key = verb as CollectionVerb;
    // keep the non-user subjects (groups, `all`); drop the old user grants
    const kept = (original[key] ?? []).filter((s) => subjectUser(s) === null);
    const users = grants
      .filter((g) => roleDef(g.role).verbs.includes(key))
      .map((g) => userSubject(g.userId));
    next[key] = Array.from(new Set([...kept, ...users]));
  }
  return next;
}
