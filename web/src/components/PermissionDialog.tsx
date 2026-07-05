import { useEffect, useState } from "react";

import {
  ALL_VERBS,
  COLLECTION_ROLES,
  type CollectionPermission,
  type Grant,
  type RoleDef,
  type RoleId,
  type Visibility,
  grantsFromPermission,
  permissionFromGrants,
} from "../lib/permission";
import { pxToRem } from "../lib/pxToRem";
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
  onSubmit: (perm: CollectionPermission) => void;
  onClose: () => void;
}) {
  const [visibility, setVisibility] = useState<Visibility>(value.visibility);
  const [grants, setGrants] = useState<Grant[]>(() => grantsFromPermission(value, owner));
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const next = () => permissionFromGrants(visibility, grants, value);

  const toggleUser = (id: string) =>
    setGrants((g) =>
      g.some((x) => x.userId === id)
        ? g.filter((x) => x.userId !== id)
        : [...g, { userId: id, role: "viewer" }],
    );
  const setRole = (id: string, role: RoleId) =>
    setGrants((g) => g.map((x) => (x.userId === id ? { ...x, role } : x)));

  const preview = next();

  return (
    <div role="presentation" onClick={onClose} style={overlay}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Share ${resourceName}`}
        data-testid="permission-dialog"
        onClick={(e) => e.stopPropagation()}
        style={panel}
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
                      style={{ ...btn(), height: 24 }}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <button
          type="button"
          data-testid="toggle-advanced"
          onClick={() => setAdvanced((a) => !a)}
          style={{ ...btn(), alignSelf: "flex-start" }}
        >
          {advanced ? "Hide advanced" : "Show advanced"}
        </button>
        {advanced && (
          <pre data-testid="advanced-verbs" style={verbsBox}>
            {ALL_VERBS.map((verb) => `${verb}: ${preview[verb].join(", ") || "—"}`).join("\n")}
          </pre>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
          <button type="button" data-testid="permission-cancel" onClick={onClose} style={btn()}>
            Cancel
          </button>
          <button
            type="button"
            data-testid="permission-save"
            disabled={busy}
            onClick={() => onSubmit(next())}
            style={btn("primary")}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

const VISIBILITIES: { id: Visibility; label: string; hint: string }[] = [
  { id: "private", label: "Private", hint: "Only you" },
  { id: "restricted", label: "Restricted", hint: "You + specific people" },
  { id: "public", label: "Public", hint: "Everyone in the workspace" },
];

const overlay: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.4)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 200,
};

const panel: React.CSSProperties = {
  width: 480,
  maxWidth: "92vw",
  maxHeight: "82vh",
  background: "var(--white)",
  borderRadius: "var(--radius-card)",
  border: "1px solid var(--paper-3)",
  boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
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

function btn(kind?: "primary" | "danger"): React.CSSProperties {
  const base: React.CSSProperties = {
    height: 28,
    padding: "0 14px",
    fontSize: pxToRem(13),
    borderRadius: "var(--radius-btn)",
    border: "1px solid var(--paper-3)",
    cursor: "pointer",
  };
  if (kind === "primary") {
    return { ...base, background: "var(--accent)", color: "var(--white)", borderColor: "var(--accent)" };
  }
  return { ...base, background: "var(--white)", color: "var(--text-paper)" };
}
