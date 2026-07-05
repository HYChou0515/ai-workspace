/**
 * The declarative entity views (#419 §B) — the dispatcher chrome + public
 * barrel. #448 P1 split each view kind into its own file behind a
 * `viewKindRegistry`; this module keeps the shared header / quick-create /
 * invalid-record banner and delegates the body to the registered renderer, so
 * adding a kind never touches the dispatcher. Re-exports the helpers + view
 * kinds that other modules (the container, tests) import from here.
 *
 * A view spec is a small YAML doc (`view:` + `entity:` + per-view options) that
 * ships as a `views/*.ai.yaml` workspace file. The entity-bound kinds (table /
 * board / gantt) render records; `health` is cross-type (see `HealthView`).
 */

import { useState } from "react";

import type { EntityFormField } from "../../api/entities";
import { pxToRem } from "../../lib/pxToRem";
import { fieldText, parseSpan, parseViewSpec } from "./shared";
import type { EntityViewProps, ViewKind, ViewSpec } from "./types";
import { resolveViewRenderer } from "./viewKindRegistry";

// Re-exports so existing importers (`AiYamlRenderer`, tests) keep their
// `./EntityViews` entry point across the P1 file split.
export { HealthView } from "./HealthView";
export { fieldText, parseSpan, parseViewSpec };
export type { EntityViewProps, ViewKind, ViewSpec };

// ── quick-create form ──────────────────────────────────────────────────────

function CreateInput({ field, value, onChange }: { field: EntityFormField; value: string; onChange: (v: string) => void }) {
  if (field.widget === "select" && field.values && field.values.length > 0) {
    return (
      <select className="inline-edit" aria-label={field.name} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {field.values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  const type = field.widget === "date" ? "date" : field.widget === "progress" ? "number" : "text";
  const placeholder = field.widget === "daterange" ? "start/end" : field.widget === "ref" ? "#" : "";
  return (
    <input
      className="inline-edit"
      aria-label={field.name}
      type={type}
      value={value}
      placeholder={placeholder}
      required={field.required}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function QuickCreate({
  form,
  onCreate,
  busy,
}: {
  form: EntityFormField[];
  onCreate: (args: Record<string, unknown>) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  if (!open) {
    return (
      <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={() => setOpen(true)}>
        + New
      </button>
    );
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const args: Record<string, unknown> = {};
    for (const f of form) {
      const v = (draft[f.name] ?? "").trim();
      if (v !== "") args[f.name] = v;
    }
    onCreate(args);
    setDraft({});
    setOpen(false);
  };

  return (
    <form onSubmit={submit} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      {form.map((f) => (
        <label key={f.name} style={{ display: "flex", flexDirection: "column", fontSize: pxToRem(12) }}>
          <span style={{ color: "var(--text-paper-d)" }}>
            {f.name}
            {f.required ? " *" : ""}
          </span>
          <CreateInput field={f} value={draft[f.name] ?? ""} onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))} />
        </label>
      ))}
      <button type="submit" className="btn" data-variant="primary" data-size="sm" disabled={busy}>
        Create
      </button>
      <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
  );
}

// ── dispatcher ─────────────────────────────────────────────────────────────

export function EntityViewBody(props: EntityViewProps) {
  const { spec, type, entities, invalid, onCreate, busy } = props;
  const renderer = resolveViewRenderer(spec.view);
  const { Component } = renderer;
  const showEmpty = entities.length === 0 && !renderer.ownsEmptyState;
  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>{spec.title ?? spec.entity}</h3>
        {type && !renderer.suppressQuickCreate && <QuickCreate form={type.form} onCreate={onCreate} busy={busy} />}
      </div>
      {invalid && invalid.length > 0 && (
        <div style={{ color: "var(--warn)", marginBottom: 8, fontSize: pxToRem(13) }}>
          {invalid.length} record{invalid.length > 1 ? "s" : ""} couldn't be parsed and {invalid.length > 1 ? "are" : "is"} hidden.
        </div>
      )}
      {showEmpty ? (
        <div style={{ color: "var(--text-paper-d)" }}>No {spec.entity} records yet.</div>
      ) : (
        <Component {...props} />
      )}
    </div>
  );
}
