/**
 * Schema-driven create/edit form for an App item (#89 P7b), styled to the
 * design-handoff "Start an RCA" modal. Renders the Tier-1 `title` (accent
 * field) + `description` (brief textarea) plus the App's `layout.form` domain
 * fields — each by its schema `kind`: `select` → a segmented {@link Picker},
 * `tags` → a chip {@link TagInput}, `text` → an input. So the form is
 * App-agnostic, not RCA-hardcoded.
 *
 * Layout mirrors the design-handoff order: title → field grid (with the
 * read-only `ownerId` box tucked in as a grid cell) → profile-as-template
 * cards → description. Optional chrome makes it serve both surfaces: `profiles`
 * renders the template cards (create flow), `ownerId` the owner cell, and
 * `onCancel` a Cancel/submit footer bar. Used by AppNewItem (create) and the
 * shell's "Edit details" modal; the caller wires `onSubmit` to createAppItem /
 * updateAppItem.
 */

import type { CSSProperties, ReactNode } from "react";
import { useRef, useState } from "react";

import type { AppManifest, FieldSpec } from "../api/types";
import { useUser } from "../hooks/useUsers";
import { Icon } from "./Icon";
import { UserAvatar } from "./UserChip";
import { pxToRem } from "../lib/pxToRem";

const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  height: 38,
  padding: "0 12px",
  fontSize: pxToRem(14),
  fontFamily: "inherit",
  color: "var(--text-paper)",
  background: "var(--white)",
  border: "1px solid var(--paper-3)",
  borderRadius: "var(--radius-btn)",
  outline: "none",
};

/** Drop empty values before submit so omitted optional/enum fields take their
 * backend default — sending `severity=""` would fail msgspec enum conversion.
 * Empty arrays (`topics: []`) are dropped too so a tagless item keeps its
 * default. */
export function pruneEmpty(values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(values).filter(([, v]) => {
      if (v === "" || v == null) return false;
      if (Array.isArray(v) && v.length === 0) return false;
      return true;
    }),
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: pxToRem(10), fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-paper-d)", marginBottom: 6 }}>
        {label}
        {required && <span style={{ color: "var(--accent)", marginLeft: 4 }}>*</span>}
      </div>
      {children}
    </div>
  );
}

function Picker({ label, options, value, onChange }: { label: string; options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div role="group" aria-label={label} style={{ display: "inline-flex", flexWrap: "wrap", gap: 3, padding: 3, background: "var(--white)", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)" }}>
      {options.map((o) => {
        const active = value === o;
        return (
          <button
            key={o}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(active ? "" : o)}
            style={{ padding: "4px 10px", border: "none", borderRadius: 4, fontSize: pxToRem(12), fontFamily: "var(--font-mono)", cursor: "pointer", background: active ? "var(--ink)" : "transparent", color: active ? "var(--white)" : "var(--text-paper)" }}
          >
            {o}
          </button>
        );
      })}
    </div>
  );
}

function TagInput({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = draft.trim();
    if (t && !value.includes(t)) onChange([...value, t]);
    setDraft("");
  };
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, minHeight: 38, padding: "6px 10px", background: "var(--white)", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)" }}>
      {value.map((t) => (
        <span key={t} style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 4px 2px 8px", borderRadius: "var(--radius-chip)", background: "var(--paper-2)", fontSize: pxToRem(12) }}>
          {t}
          <button type="button" className="tag-remove" aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>
            <Icon name="x" size={10} />
          </button>
        </span>
      ))}
      <input
        aria-label={label}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
        placeholder="+ add"
        style={{ flex: 1, minWidth: 80, border: "none", outline: "none", background: "transparent", fontSize: pxToRem(13), fontFamily: "inherit", color: "var(--text-paper)" }}
      />
    </div>
  );
}

export function ItemForm({
  manifest,
  initialValues = {},
  submitLabel,
  onSubmit,
  pending = false,
  profiles,
  defaultProfile,
  ownerId,
  onCancel,
  formId,
  hideFooter = false,
}: {
  manifest: AppManifest;
  initialValues?: Record<string, unknown>;
  submitLabel: string;
  onSubmit: (values: Record<string, unknown>) => void;
  pending?: boolean;
  /** Profile-as-template cards (create flow); the choice is submitted as `profile`. */
  profiles?: { name: string; title: string; description: string }[];
  defaultProfile?: string;
  /** Render a read-only owner box (current user on create, item.owner on edit). */
  ownerId?: string;
  /** When given, render a Cancel/submit footer bar instead of a lone button. */
  onCancel?: () => void;
  /** `id` on the `<form>` so a pinned footer button outside it can `form=…` submit. */
  formId?: string;
  /** Suppress the in-form footer — the host (e.g. the create modal) renders its own. */
  hideFooter?: boolean;
}) {
  const byName = new Map<string, FieldSpec>(manifest.fields.map((f) => [f.name, f]));
  const domainNames = (manifest.layout.form ?? []).filter((n) => n !== "title" && n !== "description");
  const labelOf = (n: string) => manifest.labels[n] ?? byName.get(n)?.label ?? n;

  const [profile, setProfile] = useState(() => defaultProfile || profiles?.[0]?.name || "");
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const seed: Record<string, unknown> = {};
    for (const n of ["title", "description", ...domainNames]) {
      const isTags = byName.get(n)?.kind === "tags";
      seed[n] = initialValues[n] ?? (isTags ? [] : "");
    }
    return seed;
  });
  const set = (n: string, v: unknown) => setValues((prev) => ({ ...prev, [n]: v }));
  const [titleError, setTitleError] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  const ownerUser = useUser(ownerId ?? "");
  const hasDescription = byName.has("description");

  return (
    <form
      id={formId}
      onSubmit={(e) => {
        e.preventDefault();
        if (!String(values.title ?? "").trim()) {
          setTitleError(true);
          titleRef.current?.focus();
          return;
        }
        setTitleError(false);
        onSubmit(profiles && profiles.length > 0 ? { ...values, profile } : values);
      }}
      style={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      <Field label={labelOf("title")} required>
        <input
          ref={titleRef}
          aria-label={labelOf("title")}
          value={String(values.title ?? "")}
          onChange={(e) => {
            set("title", e.target.value);
            if (titleError) setTitleError(false);
          }}
          placeholder={`Name this ${manifest.item.noun.toLowerCase()}`}
          style={{ ...inputStyle, border: `1.5px solid ${titleError ? "var(--err)" : "var(--accent)"}` }}
        />
        {titleError && (
          <div style={{ fontSize: pxToRem(11), color: "var(--err)", marginTop: 4 }}>Title is required</div>
        )}
      </Field>

      {(domainNames.length > 0 || ownerId) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          {domainNames.map((n) => {
            const field = byName.get(n);
            if (!field) return null;
            const label = labelOf(n);
            return (
              <Field key={n} label={label}>
                {field.kind === "select" ? (
                  <Picker label={label} options={field.options ?? []} value={String(values[n] ?? "")} onChange={(v) => set(n, v)} />
                ) : field.kind === "tags" ? (
                  <TagInput label={label} value={Array.isArray(values[n]) ? (values[n] as string[]) : []} onChange={(v) => set(n, v)} />
                ) : (
                  <input aria-label={label} value={String(values[n] ?? "")} onChange={(e) => set(n, e.target.value)} style={inputStyle} />
                )}
              </Field>
            );
          })}
          {ownerId && (
            <Field label="Owner">
              <div style={{ display: "flex", alignItems: "center", gap: 8, height: 38, padding: "0 10px", background: "var(--white)", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)" }}>
                <UserAvatar userId={ownerId} size={22} />
                <span style={{ fontSize: pxToRem(13) }}>{ownerUser.name}</span>
              </div>
            </Field>
          )}
        </div>
      )}

      {profiles && profiles.length > 0 && (
        <Field label="Template">
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(profiles.length, 3)}, 1fr)`, gap: 8, alignItems: "start" }}>
            {profiles.map((p) => {
              const active = profile === p.name;
              return (
                <button
                  key={p.name}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setProfile(p.name)}
                  style={{ textAlign: "left", padding: 12, background: active ? "var(--accent-soft)" : "var(--white)", border: `1.5px solid ${active ? "var(--accent)" : "var(--paper-3)"}`, borderRadius: "var(--radius-btn)", cursor: "pointer" }}
                >
                  <div style={{ fontSize: pxToRem(13), fontWeight: 600, color: active ? "var(--accent-h)" : "var(--text-paper)", marginBottom: 2 }}>{p.title}</div>
                  {p.description && <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>{p.description}</div>}
                </button>
              );
            })}
          </div>
        </Field>
      )}

      {hasDescription && (
        <Field label={labelOf("description")}>
          <textarea
            aria-label={labelOf("description")}
            value={String(values.description ?? "")}
            onChange={(e) => set("description", e.target.value)}
            rows={3}
            style={{ ...inputStyle, height: "auto", minHeight: 84, padding: 12, lineHeight: 1.5, resize: "vertical" }}
          />
        </Field>
      )}

      {hideFooter ? null : onCancel ? (
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button type="button" className="btn" data-variant="ghost" data-size="md" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn" data-variant="primary" data-size="md" disabled={pending}>
            {pending ? "Saving…" : submitLabel}
          </button>
        </div>
      ) : (
        <button type="submit" className="btn" data-variant="primary" data-size="md" disabled={pending} style={{ marginTop: 4 }}>
          {pending ? "Saving…" : submitLabel}
        </button>
      )}
    </form>
  );
}
