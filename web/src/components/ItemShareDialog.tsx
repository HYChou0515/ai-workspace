import { useState } from "react";

import type { PickableGroup } from "../api/groups";
import {
  ITEM_ROLES,
  ITEM_ROLE_VERBS,
  type ItemGrant,
  type ItemGroupGrant,
  type ItemPermission,
  type ItemRoleId,
  type ItemVerb,
  type ItemVisibility,
  ITEM_VISIBILITY_HINT,
  ITEM_VISIBILITY_LABEL,
  itemGrantsFromPermission,
  itemGroupGrantsFromPermission,
  itemPermissionFromGrants,
  itemRoleDef,
} from "../lib/itemPermission";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";
import { ModalActions } from "./ModalActions";
import { ModalShell } from "./ModalShell";
import { ShareTabs } from "./ShareTabs";
import { UserChip } from "./UserChip";
import { GroupPicker } from "./GroupPicker";
import { UserPicker } from "./UserPicker";

/** #306 PR3 — the per-WorkItem sharing dialog (grill D2). Presentational: takes the
 * item's CURRENT permission, hands the caller the NEXT one on save. Roles are the
 * primary control — the nested ladder (Discoverable → In workspace → Reader →
 * Participant → Collaborator) — and a "Custom" role reveals per-verb checkboxes so
 * a non-nested combination ("enter the chat but not the files") is expressible. */
export function ItemShareDialog({
  itemName,
  owner,
  value,
  busy = false,
  error = null,
  pickableGroups = [],
  onSubmit,
  onClose,
}: {
  itemName: string;
  owner: string;
  value: ItemPermission;
  busy?: boolean;
  /** Why the last save failed (e.g. a 403 for a revoked delegate). Rendered in
   * the dialog, which stays open — a silent failure is indistinguishable from
   * "the setting didn't stick". */
  error?: string | null;
  /** #608 — every group the caller may grant to (name + count). Empty ⇒ hidden. */
  pickableGroups?: PickableGroup[];
  onSubmit: (perm: ItemPermission) => void;
  onClose: () => void;
}) {
  const [visibility, setVisibility] = useState<ItemVisibility>(value.visibility);
  const [grants, setGrants] = useState<ItemGrant[]>(() => itemGrantsFromPermission(value, owner));
  const [groupGrants, setGroupGrants] = useState<ItemGroupGrant[]>(() =>
    itemGroupGrantsFromPermission(value),
  );

  // People and Groups are tabs, not stacked sections: one panel holding both
  // pickers and both lists spends more height than a laptop has, and it was the
  // Groups half that got pushed out of sight. Without groups to grant there is
  // nothing to switch between, so the strip stays hidden and People just shows.
  const [tab, setTab] = useState<"people" | "groups">(() =>
    itemGrantsFromPermission(value, owner).length === 0 &&
    itemGroupGrantsFromPermission(value).length > 0
      ? "groups"
      : "people",
  );
  const hasGroups = pickableGroups.length > 0;
  const showPeople = !hasGroups || tab === "people";
  const showGroups = hasGroups && tab === "groups";

  const next = () => itemPermissionFromGrants(visibility, grants, value, groupGrants);
  // Unresolvable (deleted / not visible) → "Unknown group", still removable (#608).
  const groupName = (id: string) =>
    pickableGroups.find((g) => g.resource_id === id)?.name ?? "Unknown group";
  const groupCount = (id: string): number | null =>
    pickableGroups.find((g) => g.resource_id === id)?.member_count ?? null;
  const addGroup = (id: string) =>
    setGroupGrants((g) =>
      id && !g.some((x) => x.groupId === id) ? [...g, { groupId: id, role: "participant" }] : g,
    );
  const setGroupRole = (id: string, role: ItemRoleId) =>
    setGroupGrants((g) => g.map((x) => (x.groupId === id ? { ...x, role } : x)));
  const removeGroup = (id: string) => setGroupGrants((g) => g.filter((x) => x.groupId !== id));

  const toggleUser = (id: string) =>
    setGrants((g) =>
      g.some((x) => x.userId === id)
        ? g.filter((x) => x.userId !== id)
        : [...g, { userId: id, role: "participant", verbs: new Set<string>() }],
    );
  const setRole = (id: string, role: ItemRoleId | "custom") =>
    setGrants((g) =>
      g.map((x) =>
        x.userId === id
          ? role === "custom"
            ? { ...x, verbs: new Set(x.verbs.size ? x.verbs : itemRoleDef(x.role).verbs) }
            : { ...x, role, verbs: new Set<string>() }
          : x,
      ),
    );
  const toggleVerb = (id: string, verb: ItemVerb) =>
    setGrants((g) =>
      g.map((x) => {
        if (x.userId !== id) return x;
        const verbs = new Set(x.verbs);
        if (verbs.has(verb)) verbs.delete(verb);
        else verbs.add(verb);
        return { ...x, verbs };
      }),
    );

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={`Share ${itemName}`}
      data-testid="item-share-dialog"
      width={480}
      maxWidth="92vw"
      panelStyle={panel}
    >
      <strong style={{ fontSize: pxToRem(14) }}>Share “{itemName}”</strong>
      <p style={caption}>Choose who can enter this workspace, read its files, and talk to the agent.</p>

      <fieldset style={{ border: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
        {VISIBILITIES.map((v) => (
          <label key={v.id} style={radioRow}>
            <input
              type="radio"
              name="item-visibility"
              data-testid={`item-visibility-${v.id}`}
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
        // flexShrink 0, NOT minHeight 0: the panel is a flex column with a
        // max-height, so a shrinkable child is compressed instead of the panel
        // scrolling — and the compression lands on whichever child is a scroll
        // container (the picker), never on the grant list that caused it. Held
        // at its natural height, the overflow reaches ModalShell's own
        // `overflowY: auto`, which is where it was always meant to go.
        <div data-testid="item-share-grants" style={{ display: "grid", gap: 8, flexShrink: 0 }}>
          {hasGroups && (
            <ShareTabs
              value={tab}
              onChange={(id) => setTab(id as "people" | "groups")}
              tabs={[
                { id: "people", label: "People", count: grants.length },
                { id: "groups", label: "Groups", count: groupGrants.length },
              ]}
            />
          )}

          {showPeople && (
          <div
            data-testid="item-share-people"
            role={hasGroups ? "tabpanel" : undefined}
            id="share-panel-people"
            aria-labelledby={hasGroups ? "share-tab-people" : undefined}
            style={{ display: "grid", gap: 8 }}
          >
          {/* No scroll box here: UserPicker caps and scrolls its own result
              list, so a second scrollable layer only ever scrolls the search
              input itself out of view when you click someone far down. */}
          <div data-testid="item-people-picker">
            <UserPicker
              selected={grants.map((g) => g.userId)}
              exclude={[owner]}
              onToggle={toggleUser}
              placeholder="Add people…"
            />
          </div>
          {grants.length > 0 && (
            // Capped + scrollable so the tenth person cannot push the picker,
            // the group section and the buttons off the panel: the list grows
            // inside its own box, everything around it stays where it was.
            <ul data-testid="item-grant-list" style={{ ...grantList, gap: 6 }}>
              {grants.map((g) => {
                const custom = g.verbs.size > 0;
                return (
                  <li key={g.userId} style={{ display: "grid", gap: 4 }}>
                    <div style={grantRow}>
                      <UserChip userId={g.userId} />
                      <select
                        aria-label={`Role for ${g.userId}`}
                        data-testid={`item-role-${g.userId}`}
                        value={custom ? "custom" : g.role}
                        onChange={(e) => setRole(g.userId, e.target.value as ItemRoleId | "custom")}
                        className="inline-edit"
                        style={{ marginLeft: "auto", fontSize: pxToRem(12) }}
                      >
                        {ITEM_ROLES.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.label}
                          </option>
                        ))}
                        <option value="custom">Custom…</option>
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
                    </div>
                    {!custom && <span style={roleHint}>{itemRoleDef(g.role).hint}</span>}
                    {custom && (
                      <div data-testid={`item-custom-${g.userId}`} style={customBox}>
                        {ITEM_ROLE_VERBS.map((verb) => (
                          <label key={verb} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: pxToRem(11) }}>
                            <input
                              type="checkbox"
                              checked={g.verbs.has(verb)}
                              onChange={() => toggleVerb(g.userId, verb)}
                            />
                            {verb}
                          </label>
                        ))}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          </div>
          )}

          {showGroups && (
            <div
              data-testid="item-share-groups"
              role="tabpanel"
              id="share-panel-groups"
              aria-labelledby="share-tab-groups"
              style={{ display: "grid", gap: 6 }}
            >
              {/* No heading: the tab above already says Groups, and the picker
                  names itself from its placeholder. */}
              <div data-testid="item-group-select">
                <GroupPicker
                  groups={pickableGroups}
                  exclude={groupGrants.map((x) => x.groupId)}
                  onPick={addGroup}
                  placeholder="Add a group…"
                />
              </div>
              {groupGrants.length > 0 && (
                <ul data-testid="item-group-list" style={{ ...grantList, gap: 4 }}>
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
                          aria-label={`Role for ${groupName(g.groupId)}`}
                          data-testid={`item-group-role-${g.groupId}`}
                          value={g.role}
                          className="inline-edit"
                          onChange={(e) => setGroupRole(g.groupId, e.target.value as ItemRoleId)}
                          style={{ marginLeft: "auto", fontSize: pxToRem(12) }}
                        >
                          {ITEM_ROLES.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.label}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          data-testid={`item-group-remove-${g.groupId}`}
                          aria-label={`Remove ${groupName(g.groupId)}`}
                          onClick={() => removeGroup(g.groupId)}
                          className="btn"
                          data-variant="danger"
                          data-size="sm"
                        >
                          Remove
                        </button>
                      </div>
                      <span style={roleHint}>{itemRoleDef(g.role).hint}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {error && (
        <p data-testid="item-share-error" role="alert" style={errorText}>
          {error}
        </p>
      )}

      <ModalActions>
        <button type="button" data-testid="item-share-cancel" onClick={onClose} className="btn" data-variant="secondary" data-size="sm">
          Cancel
        </button>
        <button
          type="button"
          data-testid="item-share-save"
          disabled={busy}
          onClick={() => onSubmit(next())}
          className="btn"
          data-variant="primary"
          data-size="sm"
        >
          Save
        </button>
      </ModalActions>
    </ModalShell>
  );
}

// #578: copy comes from the one shared table, so the chip on the item row and
// this dialog — reachable from that same row — can never disagree about what an
// item's access means.
const VISIBILITIES: { id: ItemVisibility; label: string; hint: string }[] = (
  ["private", "restricted", "public"] as const
).map((id) => ({ id, label: ITEM_VISIBILITY_LABEL[id], hint: ITEM_VISIBILITY_HINT[id] }));

const panel: React.CSSProperties = { padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 };
const caption: React.CSSProperties = { margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 };
const errorText: React.CSSProperties = { margin: 0, fontSize: pxToRem(12), color: "var(--err)", lineHeight: 1.5 };
const radioRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const grantRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const grantList: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "grid",
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
const customBox: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  padding: "6px 8px",
  marginLeft: 24,
  background: "var(--paper-2)",
  borderRadius: "var(--radius-btn)",
};
