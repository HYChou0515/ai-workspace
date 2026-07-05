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

import type { EntityDiagnostic, EntityFormField } from "../../api/entities";
import type { User } from "../../api/types";
import { pxToRem } from "../../lib/pxToRem";
import { RoleCreateInput, type WidgetKind } from "./roleWidget";
import { fieldText, parseSpan, parseViewSpec } from "./shared";
import type { EntityViewProps, ViewKind, ViewSpec } from "./types";
import { resolveViewRenderer } from "./viewKindRegistry";

// Re-exports so existing importers (`AiYamlRenderer`, tests) keep their
// `./EntityViews` entry point across the P1 file split.
export { HealthView } from "./HealthView";
export { fieldText, parseSpan, parseViewSpec };
export type { EntityViewProps, ViewKind, ViewSpec };

// ── quick-create form ──────────────────────────────────────────────────────

export function QuickCreate({
  form,
  users,
  onCreate,
  busy,
}: {
  form: EntityFormField[];
  users?: User[];
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
          <RoleCreateInput
            widget={f.widget as WidgetKind}
            name={f.name}
            value={draft[f.name] ?? ""}
            values={f.values}
            users={users}
            required={f.required}
            onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))}
          />
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

// ── conflict banner (§B2) ──────────────────────────────────────────────────

/** A non-blocking alert for records whose optimistic-lock write hit a 409. The
 * write hook has already reloaded the row to the other person's value; this just
 * tells the user their edit didn't land and lets them dismiss per record. */
function ConflictBanner({ conflicts, onDismiss }: { conflicts: number[]; onDismiss?: (number: number) => void }) {
  return (
    <div
      role="alert"
      style={{ border: "1px solid var(--warn)", borderRadius: 6, padding: 8, marginBottom: 8, fontSize: pxToRem(13) }}
    >
      Someone else changed {conflicts.length === 1 ? "this record" : "these records"} — your edit wasn't applied and the
      latest {conflicts.length === 1 ? "value was" : "values were"} reloaded.
      <span style={{ marginLeft: 8, display: "inline-flex", gap: 4 }}>
        {conflicts.map((n) => (
          <button
            key={n}
            type="button"
            className="btn"
            data-variant="ghost"
            data-size="sm"
            aria-label={`dismiss conflict ${n}`}
            onClick={() => onDismiss?.(n)}
          >
            #{n} ✕
          </button>
        ))}
      </span>
    </div>
  );
}

// ── diagnostics (§D) ────────────────────────────────────────────────────────

/** A schema/view-level Diagnostic list (warning = yellow, still usable; error =
 * red, dropped from the projection) — the "warning-not-death" surface (§D). */
function DiagnosticBanner({ diagnostics }: { diagnostics: EntityDiagnostic[] }) {
  return (
    <ul
      role="status"
      style={{ listStyle: "none", padding: 8, margin: "0 0 8px", border: "1px solid var(--paper-3)", borderRadius: 6, fontSize: pxToRem(13) }}
    >
      {diagnostics.map((d, i) => (
        <li key={i} style={{ color: d.level === "error" ? "var(--err)" : "var(--warn)" }}>
          <strong>{d.level}</strong>: {d.message}
          {d.field ? ` (${d.field})` : ""}
        </li>
      ))}
    </ul>
  );
}

// ── dispatcher ─────────────────────────────────────────────────────────────

export type EntityViewBodyProps = EntityViewProps & {
  /** Record numbers whose write hit a 409 (§B2), shown as a dismissable banner. */
  conflicts?: number[];
  onDismissConflict?: (number: number) => void;
  /** Schema/catalog-level diagnostics (§D schema layer). */
  catalogDiagnostics?: EntityDiagnostic[];
  /** The entity type has no usable schema — degrade to raw, read-only fields. */
  schemaMissing?: boolean;
};

export function EntityViewBody(props: EntityViewBodyProps) {
  const { spec, type, entities, invalid, users, onCreate, busy, conflicts, onDismissConflict, catalogDiagnostics, schemaMissing } =
    props;
  const canWrite = props.canWrite !== false; // omitted ≡ writable (§E)
  const renderer = resolveViewRenderer(spec.view);
  const { Component } = renderer;
  const showEmpty = entities.length === 0 && !renderer.ownsEmptyState;
  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>{spec.title ?? spec.entity}</h3>
        {type && !renderer.suppressQuickCreate && canWrite && (
          <QuickCreate form={type.form} users={users} onCreate={onCreate} busy={busy} />
        )}
      </div>
      {conflicts && conflicts.length > 0 && <ConflictBanner conflicts={conflicts} onDismiss={onDismissConflict} />}
      {catalogDiagnostics && catalogDiagnostics.length > 0 && <DiagnosticBanner diagnostics={catalogDiagnostics} />}
      {schemaMissing && (
        <div style={{ color: "var(--warn)", marginBottom: 8, fontSize: pxToRem(13) }}>
          No schema for {spec.entity} — showing raw fields (read-only).
        </div>
      )}
      {invalid && invalid.length > 0 && (
        <div style={{ color: "var(--warn)", marginBottom: 8, fontSize: pxToRem(13) }}>
          {invalid.length} record{invalid.length > 1 ? "s" : ""} couldn't be parsed and {invalid.length > 1 ? "are" : "is"} excluded
          from the projection.
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
