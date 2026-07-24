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
import { ModalShell } from "../../components/ModalShell";
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
  entityLabel,
}: {
  form: EntityFormField[];
  users?: User[];
  onCreate: (args: Record<string, unknown>) => void;
  busy?: boolean;
  /** Entity name for the modal title ("New issue"). */
  entityLabel?: string;
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

  const close = () => {
    setDraft({});
    setOpen(false);
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const args: Record<string, unknown> = {};
    for (const f of form) {
      const v = (draft[f.name] ?? "").trim();
      if (v !== "") args[f.name] = v;
    }
    onCreate(args);
    close();
  };

  // #2: the expanded form used to sit in the panel's flex header, so on the board
  // it floated as a lopsided card next to a vertically-centred title. It now opens
  // in a modal — the header keeps just the "+ New" button.
  return (
    <ModalShell onClose={close} ariaLabel={`New ${entityLabel ?? "record"}`} width={560} align="top">
      <form onSubmit={submit} className="ev-quickcreate">
        <h3 className="ev-quickcreate__title">New {entityLabel ?? "record"}</h3>
        <div className="ev-quickcreate__grid">
          {form.map((f) => (
            <label key={f.name} className="ev-quickcreate__field">
              <span className="ev-quickcreate__label">
                {f.name}
                {f.required ? <span className="ev-quickcreate__req"> *</span> : ""}
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
        </div>
        <div className="ev-quickcreate__actions">
          <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={close}>
            Cancel
          </button>
          <button type="submit" className="btn" data-variant="primary" data-size="sm" disabled={busy}>
            Create
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

// ── conflict banner (§B2) ──────────────────────────────────────────────────

/** A non-blocking alert for records whose optimistic-lock write hit a 409. The
 * write hook has already reloaded the row to the other person's value; this just
 * tells the user their edit didn't land and lets them dismiss per record. */
function ConflictBanner({ conflicts, onDismiss }: { conflicts: number[]; onDismiss?: (number: number) => void }) {
  return (
    <div role="alert" className="ev-banner">
      <span className="ev-banner__icon" aria-hidden>
        ⚠
      </span>
      <div className="ev-banner__body">
        Someone else changed {conflicts.length === 1 ? "this record" : "these records"} — your edit wasn't applied and the
        latest {conflicts.length === 1 ? "value was" : "values were"} reloaded.
        <span className="ev-banner__actions">
          {conflicts.map((n) => (
            <button
              key={n}
              type="button"
              className="ev-banner__chip"
              aria-label={`dismiss conflict ${n}`}
              onClick={() => onDismiss?.(n)}
            >
              #{n} ✕
            </button>
          ))}
        </span>
      </div>
    </div>
  );
}

// ── diagnostics (§D) ────────────────────────────────────────────────────────

/** A schema/view-level Diagnostic list (warning = yellow, still usable; error =
 * red, dropped from the projection) — the "warning-not-death" surface (§D). */
function DiagnosticBanner({ diagnostics }: { diagnostics: EntityDiagnostic[] }) {
  const hasError = diagnostics.some((d) => d.level === "error");
  return (
    <div role="status" className={`ev-banner${hasError ? " ev-banner--err" : ""}`}>
      <ul className="ev-diags">
        {diagnostics.map((d, i) => (
          <li key={i} className="ev-diags__item">
            <span className={`ev-level ev-level--${d.level === "error" ? "error" : "warning"}`}>{d.level}</span>
            <span>
              {d.message}
              {d.field ? ` (${d.field})` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
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
    <div className="ev-panel">
      <div className="ev-panel__head">
        <h3 className="ev-panel__title">
          {spec.title ?? spec.entity}
          {entities.length > 0 && <span className="ev-panel__count">{entities.length}</span>}
        </h3>
        {type && !renderer.suppressQuickCreate && canWrite && (
          <QuickCreate form={type.form} users={users} onCreate={onCreate} busy={busy} entityLabel={type.name} />
        )}
      </div>
      {conflicts && conflicts.length > 0 && <ConflictBanner conflicts={conflicts} onDismiss={onDismissConflict} />}
      {catalogDiagnostics && catalogDiagnostics.length > 0 && <DiagnosticBanner diagnostics={catalogDiagnostics} />}
      {schemaMissing && (
        <div role="status" className="ev-banner">
          <span className="ev-banner__icon" aria-hidden>
            ⚠
          </span>
          <div className="ev-banner__body">No schema for {spec.entity} — showing raw fields (read-only).</div>
        </div>
      )}
      {invalid && invalid.length > 0 && (
        <div role="status" className="ev-banner">
          <span className="ev-banner__icon" aria-hidden>
            ⚠
          </span>
          <div className="ev-banner__body">
            {invalid.length} record{invalid.length > 1 ? "s" : ""} couldn't be parsed and{" "}
            {invalid.length > 1 ? "are" : "is"} excluded from the projection.
          </div>
        </div>
      )}
      {showEmpty ? (
        <div className="ev-empty">
          <span className="ev-empty__icon" aria-hidden>
            ◇
          </span>
          <div>No {spec.entity} records yet.</div>
        </div>
      ) : (
        <Component {...props} />
      )}
    </div>
  );
}
