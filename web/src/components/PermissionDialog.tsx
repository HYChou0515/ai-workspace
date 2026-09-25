import { useRef, useState } from "react";

import type { PickableGroup } from "../api/groups";
import {
  ALL_VERBS,
  COLLECTION_ROLES,
  type CollectionPermission,
  type Grant,
  type GroupGrant,
  type RoleDef,
  type RoleId,
  type Visibility,
  grantsFromPermission,
  groupGrantsFromPermission,
  permissionFromGrants,
  previewSubjects,
  roleDef,
} from "../lib/permission";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { sameShape } from "../lib/sameShape";
import { Icon } from "./Icon";
import { ModalActions } from "./ModalActions";
import { ModalShell } from "./ModalShell";
import { ShareTabs } from "./ShareTabs";
import { UserChip } from "./UserChip";
import { GroupPicker } from "./GroupPicker";
import { UserPicker } from "./UserPicker";

/** #310 — the generic sharing dialog. Presentational: it takes the CURRENT
 * permission and hands the caller the NEXT one on save, so the same dialog drives
 * a collection now (and a chat / work-item once their setters land). Roles are the
 * primary control (Viewer / Collaborator / Editor); an "Advanced" panel reveals
 * the exact verb grants each role maps to. Visibility gates whether the grant list
 * is enforced. */
export function PermissionDialog({
  resourceName,
  owner,
  value,
  busy = false,
  roles = COLLECTION_ROLES,
  caption: captionText,
  audience = "workspace",
  error = null,
  pickableGroups = [],
  onSubmit,
  onClose,
}: {
  resourceName: string;
  /** The resource owner — never listed as a grantee (they hold everything). */
  owner: string;
  value: CollectionPermission;
  busy?: boolean;
  /** The roles offered in the grant picker. Defaults to the three collection
   * roles; a per-doc override (#308) passes `DOC_ROLES` (Viewer only). */
  roles?: RoleDef[];
  /** Sub-heading under the title — resource-specific copy. Defaults to the
   * collection's sentence. */
  caption?: string;
  /** Who "Public" reaches (plan-skill-hub-ui-polish D12): a collection or an
   * item is public to this workspace; a skill hub entry to everyone on the
   * platform. The hint under the Public option says which. */
  audience?: "workspace" | "platform";
  /** The last save's refusal, worded — drawn INSIDE the dialog above Save,
   * because the dialog stays open on failure and a line on the page behind
   * the backdrop is one the person cannot see (#826 round 2). */
  error?: string | null;
  /** #608 — every group the caller may grant to (name + count). Empty ⇒ the group
   * section is hidden (the caller didn't load them / the feature is off). */
  pickableGroups?: PickableGroup[];
  onSubmit: (perm: CollectionPermission) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [visibility, setVisibility] = useState<Visibility>(value.visibility);
  const [grants, setGrants] = useState<Grant[]>(() => grantsFromPermission(value, owner));
  const [groupGrants, setGroupGrants] = useState<GroupGrant[]>(() =>
    groupGrantsFromPermission(value),
  );
  const [advanced, setAdvanced] = useState(false);

  // People / Groups as tabs — see ItemShareDialog: stacked, the Groups half
  // ends up below the fold. Opens on whichever side already has grants.
  const [tab, setTab] = useState<"people" | "groups">(() =>
    grantsFromPermission(value, owner).length === 0 &&
    groupGrantsFromPermission(value).length > 0
      ? "groups"
      : "people",
  );
  const hasGroups = pickableGroups.length > 0;
  const showPeople = !hasGroups || tab === "people";
  const showGroups = hasGroups && tab === "groups";

  const next = () => permissionFromGrants(visibility, grants, value, groupGrants);

  // #779: against the opening seed rather than `next()`, which normalises — see
  // ItemShareDialog for the same reasoning. Same quiet failure too: the dialog
  // closes and the access is simply unchanged, with nothing said.
  const initialRef = useRef({
    visibility: value.visibility,
    grants: grantsFromPermission(value, owner),
    groupGrants: groupGrantsFromPermission(value),
  });
  // sameShape, not JSON.stringify: `grants` reorders when a subject is removed
  // and re-added (a false "dirty"), and ItemGrant.verbs is a Set, which
  // stringifies to {} however full it is — so a whole custom-verb edit read as
  // unchanged and closed without asking.
  const dirty = !sameShape({ visibility, grants, groupGrants }, initialRef.current);
  const attemptClose = useDirtyClose(dirty, onClose);
  // A grant whose group we can't resolve (deleted, or not visible to us) reads as
  // "Unknown group" — the owner can still remove it — rather than a raw id (#608).
  const groupName = (id: string) =>
    pickableGroups.find((g) => g.resource_id === id)?.name ??
    t("perm.group.unknown");
  const groupCount = (id: string): number | null =>
    pickableGroups.find((g) => g.resource_id === id)?.member_count ?? null;

  const toggleUser = (id: string) =>
    setGrants((g) =>
      g.some((x) => x.userId === id)
        ? g.filter((x) => x.userId !== id)
        : [...g, { userId: id, role: "viewer" }],
    );
  const setRole = (id: string, role: RoleId) =>
    setGrants((g) => g.map((x) => (x.userId === id ? { ...x, role } : x)));
  const addGroup = (id: string) =>
    setGroupGrants((g) =>
      id && !g.some((x) => x.groupId === id) ? [...g, { groupId: id, role: "viewer" }] : g,
    );
  const setGroupRole = (id: string, role: RoleId) =>
    setGroupGrants((g) => g.map((x) => (x.groupId === id ? { ...x, role } : x)));
  const removeGroup = (id: string) => setGroupGrants((g) => g.filter((x) => x.groupId !== id));

  const preview = next();

  // The three visibilities, in the viewer's language; the Public hint names
  // the audience the caller declared.
  const visibilities: { id: Visibility; label: string; hint: string }[] = [
    { id: "private", label: t("perm.private"), hint: t("perm.private.hint") },
    {
      id: "restricted",
      label: t("perm.restricted"),
      hint: t("perm.restricted.hint"),
    },
    {
      id: "public",
      label: t("perm.public"),
      hint: t(
        audience === "platform"
          ? "perm.public.hint.platform"
          : "perm.public.hint.workspace",
      ),
    },
  ];
  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("perm.title", { name: resourceName })}
      data-testid="permission-dialog"
      width={480}
      maxWidth="92vw"
      panelStyle={panel}
    >
      <strong style={{ fontSize: pxToRem(14) }}>
        {t("perm.title", { name: resourceName })}
      </strong>
      <p style={caption}>{captionText ?? t("perm.caption.collection")}</p>

        <fieldset style={{ border: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
        {visibilities.map((v) => (
            <label key={v.id} style={radioRow}>
              <input
                type="radio"
                name="visibility"
                data-testid={`visibility-${v.id}`}
                checked={visibility === v.id}
                onChange={() => setVisibility(v.id)}
              />
              <span>
                <span style={{ fontSize: pxToRem(13) }}>{v.label}</span>
                <span style={{ ...caption, marginLeft: 6 }}>{v.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {visibility === "restricted" && (
          // flexShrink 0, NOT minHeight 0 — see ItemShareDialog for the whole
          // story: a shrinkable child lets the flex column compress the picker
          // instead of letting ModalShell's panel scroll.
          <div data-testid="permission-grants" style={{ display: "grid", gap: 8, flexShrink: 0 }}>
            {hasGroups && (
              <ShareTabs
                value={tab}
                onChange={(id) => setTab(id as "people" | "groups")}
                tabs={[
                {
                  id: "people",
                  label: t("perm.tab.people"),
                  count: grants.length,
                },
                {
                  id: "groups",
                  label: t("perm.tab.groups"),
                  count: groupGrants.length,
                },
                ]}
              />
            )}

            {showPeople && (
            <div
              data-testid="permission-people"
              role={hasGroups ? "tabpanel" : undefined}
              id="share-panel-people"
              aria-labelledby={hasGroups ? "share-tab-people" : undefined}
              style={{ display: "grid", gap: 8 }}
            >
            {/* No scroll box here: UserPicker caps and scrolls its own result
                list, and a second layer just scrolls the search input away. */}
            <div data-testid="permission-people-picker">
              <UserPicker
                selected={grants.map((g) => g.userId)}
                exclude={[owner]}
                onToggle={toggleUser}
                  placeholder={t("perm.addPeople")}
              />
            </div>
            {grants.length > 0 && (
              // Capped + scrollable: the list grows inside its own box, so the
              // picker, the group section and the buttons stay put.
              <ul className="scrollable" data-testid="grant-list" style={grantList}>
                {grants.map((g) => (
                  <li key={g.userId} style={{ display: "grid", gap: 2 }}>
                    <div style={grantRow}>
                      <UserChip userId={g.userId} />
                      <select
                          aria-label={t("perm.roleFor", { name: g.userId })}
                        data-testid={`role-${g.userId}`}
                        value={g.role}
                        onChange={(e) => setRole(g.userId, e.target.value as RoleId)}
                        className="input inline-edit"
                        style={{ marginLeft: "auto", fontSize: pxToRem(12) }}
                      >
                        {roles.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                          aria-label={t("perm.remove", { name: g.userId })}
                        onClick={() => toggleUser(g.userId)}
                        className="btn"
                        data-variant="danger"
                        data-size="sm"
                      >
                          {t("perm.remove.button")}
                      </button>
                    </div>
                    <span style={roleHint}>{roleDef(g.role).hint}</span>
                  </li>
                ))}
              </ul>
            )}

            </div>
            )}

            {showGroups && (
              <div
                data-testid="permission-groups"
                role="tabpanel"
                id="share-panel-groups"
                aria-labelledby="share-tab-groups"
                style={{ display: "grid", gap: 6 }}
              >
                {/* No heading: the tab above already says Groups. */}
                <div data-testid="group-grant-select">
                  <GroupPicker
                    groups={pickableGroups}
                    exclude={groupGrants.map((x) => x.groupId)}
                    onPick={addGroup}
                  placeholder={t("perm.addGroup")}
                  />
                </div>
                {groupGrants.length > 0 && (
                  <ul className="scrollable" data-testid="group-grant-list" style={grantList}>
                    {groupGrants.map((g) => (
                      <li key={g.groupId} style={{ display: "grid", gap: 2 }}>
                        <div style={grantRow}>
                          <span style={groupPill}>
                            <Icon name="users" size={13} color="var(--text-paper-d)" />
                            <span style={{ fontSize: pxToRem(13) }}>{groupName(g.groupId)}</span>
                            {groupCount(g.groupId) != null && (
                              <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>
                                · {groupCount(g.groupId)}
                              </span>
                            )}
                          </span>
                          <select
                          aria-label={t("perm.roleFor", {
                            name: groupName(g.groupId),
                          })}
                            data-testid={`group-role-${g.groupId}`}
                            value={g.role}
                            onChange={(e) => setGroupRole(g.groupId, e.target.value as RoleId)}
                            className="input inline-edit"
                            style={{ marginLeft: "auto", fontSize: pxToRem(12) }}
                          >
                            {roles.map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.label}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            data-testid={`group-remove-${g.groupId}`}
                          aria-label={t("perm.remove", {
                            name: groupName(g.groupId),
                          })}
                            onClick={() => removeGroup(g.groupId)}
                            className="btn"
                            data-variant="danger"
                            data-size="sm"
                          >
                          {t("perm.remove.button")}
                          </button>
                        </div>
                        <span style={roleHint}>{roleDef(g.role).hint}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        <button
          type="button"
          data-testid="toggle-advanced"
          onClick={() => setAdvanced((a) => !a)}
          className="btn"
          data-variant="secondary"
          data-size="sm"
          style={{ alignSelf: "flex-start" }}
        >
        {t(advanced ? "perm.advanced.hide" : "perm.advanced.show")}
        </button>
        {advanced && (
          <pre data-testid="advanced-verbs" style={verbsBox}>
            {ALL_VERBS.map(
              (verb) => `${verb}: ${previewSubjects(visibility, preview, verb).join(", ") || "—"}`,
            ).join("\n")}
          </pre>
        )}

        {error ? (
          <p className="error" role="alert" style={{ margin: 0, fontSize: pxToRem(12) }}>
            {error}
          </p>
        ) : null}

        <ModalActions>
          <button
            type="button"
            data-testid="permission-cancel"
            onClick={attemptClose}
            className="btn"
            data-variant="secondary"
            data-size="sm"
          >
          {t("perm.cancel")}
          </button>
          <button
            type="button"
            data-testid="permission-save"
            disabled={busy}
            onClick={() => onSubmit(next())}
            className="btn"
            data-variant="primary"
            data-size="sm"
          >
          {t("perm.save")}
          </button>
        </ModalActions>
    </ModalShell>
  );
}


const panel: React.CSSProperties = {
  padding: 18,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  minHeight: 0,
};

const caption: React.CSSProperties = {
  margin: 0,
  fontSize: pxToRem(12),
  color: "var(--text-paper-d)",
  lineHeight: 1.5,
};

const radioRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };

const grantRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const grantList: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "grid",
  gap: 4,
  maxHeight: "30vh",
  overflow: "auto",
};
const roleHint: React.CSSProperties = {
  paddingLeft: 2,
  fontSize: pxToRem(11),
  color: "var(--text-paper-d2)",
};
const groupPill: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "3px 8px",
  borderRadius: 999,
  background: "var(--paper-2)",
};

const verbsBox: React.CSSProperties = {
  margin: 0,
  padding: 10,
  background: "var(--paper-2)",
  borderRadius: "var(--radius-btn)",
  fontSize: pxToRem(11),
  color: "var(--text-paper-d)",
  overflowX: "auto",
};
