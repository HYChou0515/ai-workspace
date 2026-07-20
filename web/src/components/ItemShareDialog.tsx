import { useState } from "react";

import {
  ITEM_ROLES,
  ITEM_ROLE_VERBS,
  type ItemGrant,
  type ItemPermission,
  type ItemRoleId,
  type ItemVerb,
  type ItemVisibility,
  itemGrantsFromPermission,
  itemPermissionFromGrants,
  itemRoleDef,
} from "../lib/itemPermission";
import { pxToRem } from "../lib/pxToRem";
import { ModalShell } from "./ModalShell";
import { UserChip } from "./UserChip";
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
  onSubmit: (perm: ItemPermission) => void;
  onClose: () => void;
}) {
  const [visibility, setVisibility] = useState<ItemVisibility>(value.visibility);
  const [grants, setGrants] = useState<ItemGrant[]>(() => itemGrantsFromPermission(value, owner));

  const next = () => itemPermissionFromGrants(visibility, grants, value);

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
            <ul data-testid="item-grant-list" style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
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

      {error && (
        <p data-testid="item-share-error" role="alert" style={errorText}>
          {error}
        </p>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
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
      </div>
    </ModalShell>
  );
}

const VISIBILITIES: { id: ItemVisibility; label: string; hint: string }[] = [
  { id: "private", label: "Private", hint: "Only you" },
  { id: "restricted", label: "Restricted", hint: "You + specific people" },
  { id: "public", label: "Public", hint: "Everyone in the workspace" },
];

const panel: React.CSSProperties = { padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 };
const caption: React.CSSProperties = { margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 };
const errorText: React.CSSProperties = { margin: 0, fontSize: pxToRem(12), color: "var(--err)", lineHeight: 1.5 };
const radioRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const grantRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const customBox: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  padding: "6px 8px",
  marginLeft: 24,
  background: "var(--paper-2)",
  borderRadius: "var(--radius-btn)",
};
