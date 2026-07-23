import { useState } from "react";

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
} from "../lib/permission";
import { pxToRem } from "../lib/pxToRem";
import { ModalShell } from "./ModalShell";
import { UserChip } from "./UserChip";
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
  caption: captionText = "Choose who can access this collection.",
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
  /** Sub-heading under the title — resource-specific copy. */
  caption?: string;
  /** #608 — every group the caller may grant to (name + count). Empty ⇒ the group
   * section is hidden (the caller didn't load them / the feature is off). */
  pickableGroups?: PickableGroup[];
  onSubmit: (perm: CollectionPermission) => void;
  onClose: () => void;
}) {
  const [visibility, setVisibility] = useState<Visibility>(value.visibility);
  const [grants, setGrants] = useState<Grant[]>(() => grantsFromPermission(value, owner));
  const [groupGrants, setGroupGrants] = useState<GroupGrant[]>(() =>
    groupGrantsFromPermission(value),
  );
  const [advanced, setAdvanced] = useState(false);

  const next = () => permissionFromGrants(visibility, grants, value, groupGrants);
  const groupName = (id: string) =>
    pickableGroups.find((g) => g.resource_id === id)?.name ?? id;

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

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={`Share ${resourceName}`}
      data-testid="permission-dialog"
      width={480}
      maxWidth="92vw"
      panelStyle={panel}
    >
        <strong style={{ fontSize: pxToRem(14) }}>Share “{resourceName}”</strong>
        <p style={caption}>{captionText}</p>

        <fieldset style={{ border: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
          {VISIBILITIES.map((v) => (
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
          <div style={{ display: "grid", gap: 8, minHeight: 0 }}>
            <div style={{ maxHeight: "26vh", overflow: "auto" }}>
              <UserPicker
                selected={grants.map((g) => g.userId)}
                exclude={[owner]}
                onToggle={toggleUser}
                placeholder="Add people…"
              />
            </div>
            {grants.length > 0 && (
              <ul data-testid="grant-list" style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4 }}>
                {grants.map((g) => (
                  <li key={g.userId} style={grantRow}>
                    <UserChip userId={g.userId} />
                    <select
                      aria-label={`Role for ${g.userId}`}
                      data-testid={`role-${g.userId}`}
                      value={g.role}
                      onChange={(e) => setRole(g.userId, e.target.value as RoleId)}
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
                      aria-label={`Remove ${g.userId}`}
                      onClick={() => toggleUser(g.userId)}
                      className="btn"
                      data-variant="danger"
                      data-size="sm"
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {pickableGroups.length > 0 && (
              <div style={{ display: "grid", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="caps">Groups</span>
                  <select
                    data-testid="group-grant-select"
                    aria-label="Add a group"
                    value=""
                    onChange={(e) => addGroup(e.target.value)}
                    style={{ fontSize: pxToRem(12) }}
                  >
                    <option value="">Add a group…</option>
                    {pickableGroups
                      .filter((pg) => !groupGrants.some((x) => x.groupId === pg.resource_id))
                      .map((pg) => (
                        <option key={pg.resource_id} value={pg.resource_id}>
                          {pg.name} · {pg.member_count}
                        </option>
                      ))}
                  </select>
                </div>
                {groupGrants.length > 0 && (
                  <ul
                    data-testid="group-grant-list"
                    style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4 }}
                  >
                    {groupGrants.map((g) => (
                      <li key={g.groupId} style={grantRow}>
                        <span style={{ fontSize: pxToRem(13) }}>{groupName(g.groupId)}</span>
                        <select
                          aria-label={`Role for ${groupName(g.groupId)}`}
                          data-testid={`group-role-${g.groupId}`}
                          value={g.role}
                          onChange={(e) => setGroupRole(g.groupId, e.target.value as RoleId)}
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
                          aria-label={`Remove ${groupName(g.groupId)}`}
                          onClick={() => removeGroup(g.groupId)}
                          className="btn"
                          data-variant="danger"
                          data-size="sm"
                        >
                          Remove
                        </button>
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
          {advanced ? "Hide advanced" : "Show advanced"}
        </button>
        {advanced && (
          <pre data-testid="advanced-verbs" style={verbsBox}>
            {ALL_VERBS.map(
              (verb) => `${verb}: ${previewSubjects(visibility, preview, verb).join(", ") || "—"}`,
            ).join("\n")}
          </pre>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
          <button
            type="button"
            data-testid="permission-cancel"
            onClick={onClose}
            className="btn"
            data-variant="secondary"
            data-size="sm"
          >
            Cancel
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
            Save
          </button>
        </div>
    </ModalShell>
  );
}

const VISIBILITIES: { id: Visibility; label: string; hint: string }[] = [
  { id: "private", label: "Private", hint: "Only you" },
  { id: "restricted", label: "Restricted", hint: "You + specific people" },
  { id: "public", label: "Public", hint: "Everyone in the workspace" },
];

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

const verbsBox: React.CSSProperties = {
  margin: 0,
  padding: 10,
  background: "var(--paper-2)",
  borderRadius: "var(--radius-btn)",
  fontSize: pxToRem(11),
  color: "var(--text-paper-d)",
  overflowX: "auto",
};
