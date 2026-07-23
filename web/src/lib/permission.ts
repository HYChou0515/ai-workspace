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

export type RoleId = "discoverable" | "viewer" | "collaborator" | "editor";

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
  {
    id: "discoverable",
    label: "Discoverable",
    // Permission-disclosure: read_meta ONLY — sees the collection exists and can
    // request access, but cannot read its content. Below Viewer.
    hint: "Can see it exists + request access, not read",
    verbs: ["read_meta"],
  },
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

/** #308 — a per-doc read override only TIGHTENS read access (the backend honours
 * just the read verbs on a doc), so its dialog offers a single Viewer role. Reuses
 * the Viewer def so the role⇄grant mapping is identical to a collection viewer. */
export const DOC_ROLES: RoleDef[] = COLLECTION_ROLES.filter((r) => r.id === "viewer");

/** The verbs any collection role touches — the set the dialog OWNS. Every other
 * verb on the permission is left untouched on save. */
const ROLE_VERBS: Set<string> = new Set(COLLECTION_ROLES.flatMap((r) => r.verbs));

const USER_PREFIX = "user:";
const GROUP_PREFIX = "group:";

export const userSubject = (id: string): string => `${USER_PREFIX}${id}`;
export const groupSubject = (id: string): string => `${GROUP_PREFIX}${id}`;

/** The user id inside a `user:<id>` subject, or null for a group / `all` subject. */
export const subjectUser = (subject: string): string | null =>
  subject.startsWith(USER_PREFIX) ? subject.slice(USER_PREFIX.length) : null;

/** The group id inside a `group:<id>` subject, or null otherwise (#608). */
export const subjectGroup = (subject: string): string | null =>
  subject.startsWith(GROUP_PREFIX) ? subject.slice(GROUP_PREFIX.length) : null;

export const roleDef = (id: RoleId): RoleDef =>
  COLLECTION_ROLES.find((r) => r.id === id) ?? COLLECTION_ROLES[0];

/** The highest role a set of granted verbs satisfies (by signature verb), so a
 * grant made outside the dialog (or via raw verbs) still maps to a legible role.
 * `null` when the verbs don't even reach Viewer (no read grant). */
export function roleForVerbs(verbs: Set<string>): RoleId | null {
  if (verbs.has("edit_content") || verbs.has("write_meta")) return "editor";
  if (verbs.has("add_content")) return "collaborator";
  if (verbs.has("read_content")) return "viewer";
  // read_meta WITHOUT read_content — the disclosure tier: sees it exists only.
  if (verbs.has("read_meta")) return "discoverable";
  return null;
}

export type Grant = { userId: string; role: RoleId };
/** #608 — a grant to a whole group, decoded from `group:<id>` subjects. */
export type GroupGrant = { groupId: string; role: RoleId };

/** Decode a permission's GROUP grants into (group, role) rows for the dialog
 * (#608). Mirrors {@link grantsFromPermission} but for `group:<id>` subjects;
 * `all`/`user:` subjects are ignored here. A group appears once, at its highest
 * role. */
export function groupGrantsFromPermission(perm: CollectionPermission): GroupGrant[] {
  const verbsByGroup = new Map<string, Set<string>>();
  for (const verb of ROLE_VERBS) {
    for (const subject of perm[verb as CollectionVerb] ?? []) {
      const gidv = subjectGroup(subject);
      if (gidv === null) continue;
      const set = verbsByGroup.get(gidv) ?? new Set<string>();
      set.add(verb);
      verbsByGroup.set(gidv, set);
    }
  }
  const grants: GroupGrant[] = [];
  for (const [groupId, verbs] of verbsByGroup) {
    const role = roleForVerbs(verbs);
    if (role !== null) grants.push({ groupId, role });
  }
  return grants.sort((a, b) => a.groupId.localeCompare(b.groupId));
}

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
 * the `all` wildcard. Both `user:` grants and `group:` grants (#608) of role-verbs
 * are rebuilt from `grants` / `groupGrants` — so a group grant round-trips and can
 * be removed, not just preserved. */
export function permissionFromGrants(
  visibility: Visibility,
  grants: Grant[],
  original: CollectionPermission,
  groupGrants: GroupGrant[] = [],
): CollectionPermission {
  const next: CollectionPermission = { ...original, visibility };
  for (const verb of ROLE_VERBS) {
    const key = verb as CollectionVerb;
    // keep only subjects the dialog doesn't manage (the `all` wildcard, any future
    // subject kind); drop the old user AND group grants — they're rebuilt below.
    const kept = (original[key] ?? []).filter(
      (s) => subjectUser(s) === null && subjectGroup(s) === null,
    );
    const users = grants
      .filter((g) => roleDef(g.role).verbs.includes(key))
      .map((g) => userSubject(g.userId));
    const groups = groupGrants
      .filter((g) => roleDef(g.role).verbs.includes(key))
      .map((g) => groupSubject(g.groupId));
    next[key] = Array.from(new Set([...kept, ...users, ...groups]));
  }
  return next;
}

/** The owner-or-superuser gate for management affordances whose permission
 * object the FE has NOT fetched at gate time (collection "Manage access", the
 * per-doc Permissions dialog, a chat's rename/share/delete). Mirrors
 * `perm/authorize.py` steps 2+4: a direct human superuser passes everything,
 * the owner passes their own resource.
 *
 * An UNKNOWN owner (still loading / a legacy row without meta) is NOT the
 * current user: gating on `owner ?? me` treated "don't know yet" as "mine" and
 * flashed management buttons at non-owners. A `change_permission` delegate is
 * not reflected here — these call sites have no permission object to consult —
 * so a delegate simply doesn't see the shortcut; the server still honours them.
 *
 * `isSuperuser` is REQUIRED, same reasoning as `hasItemVerb`: the whole bug
 * class this fixes is a call site that never mentioned superusers (`owner ===
 * me`), leaving admins without an entry point the backend had always allowed.
 * Prefer wiring it from `useIsSuperuser()`. */
export function canManageAccess(
  ownerId: string | null | undefined,
  currentUserId: string,
  isSuperuser: boolean,
): boolean {
  if (isSuperuser) return true; // authorize.py step 2 — before any owner fact
  return ownerId != null && ownerId === currentUserId;
}

/** Display token for "anyone in the workspace" in the advanced preview — public
 * grants a verb to everyone without listing subjects (the backend just returns
 * True), so the preview shows this rather than a user list. Display-only. */
export const EVERYONE = "everyone";

/** #460 P6 — the per-verb subjects to SHOW for a given selected `visibility`,
 * mirroring the backend `authorize()` decision (perm/authorize.py) so the
 * advanced preview tells the truth for the currently-chosen radio instead of
 * echoing the stored grant lists:
 *   • change_permission — never opened by visibility; always the grant list.
 *   • public   — everyone (all other verbs).
 *   • private  — nobody.
 *   • restricted — the per-verb grant list verbatim.
 * `perm` should be the pending permission (permissionFromGrants output) so the
 * restricted case reflects unsaved edits. */
export function previewSubjects(
  visibility: Visibility,
  perm: CollectionPermission,
  verb: keyof Omit<CollectionPermission, "visibility">,
): string[] {
  if (verb === "change_permission") return perm.change_permission;
  if (visibility === "public") return [EVERYONE];
  if (visibility === "private") return [];
  return perm[verb];
}
