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
import { refOptionsForField, type RefOption } from "./refTraversal";
import { RoleCreateInput, type WidgetKind } from "./roleWidget";
import { ConflictBanner, fieldText, parseSpan, parseViewSpec } from "./shared";
import type { EntityViewProps, ViewConfig, ViewKind, ViewSpec } from "./types";
import { ViewSettingsPanel } from "./ViewSettingsPanel";
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
  refOptionsFor,
}: {
  form: EntityFormField[];
  users?: User[];
  onCreate: (args: Record<string, unknown>) => void;
  busy?: boolean;
  /** Entity name for the modal title ("New issue"). */
  entityLabel?: string;
  /** Per-field ref picker options — a `ref` field renders a #N-title dropdown
   * instead of a raw number box when its target records are loaded. */
  refOptionsFor?: (name: string) => RefOption[] | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  if (!open) {
    return (
      <button
        type="button"
        className="btn"
        data-variant="secondary"
        data-size="sm"
        onClick={() => {
          // A new record starts TODAY, with its end left open. Nothing else can
          // be a better guess, an empty range is the one value that puts the
          // record nowhere at all, and the date is visible in the form, so it
          // is a default you can see and change rather than one applied behind
          // your back.
          const today = new Date().toISOString().slice(0, 10);
          const seed: Record<string, string> = {};
          for (const f of form) if (f.widget === "daterange") seed[f.name] = `${today}/`;
          setDraft(seed);
          setOpen(true);
        }}
      >
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
                refOptions={refOptionsFor?.(f.name)}
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

// The Group-by / Sort / Fields controls now live in the "View" gear panel
// (#GH-projects P3) — see ViewSettingsPanel, driven by the ViewConfig the
// container builds. The old standalone GroupByControl is retired.

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
  /** #GH-projects P3 — the "View" settings panel model (Group by / Sort / Fields
   * + save-to-view state); provided only to writers of a table/board view. */
  viewConfig?: ViewConfig;
};

export function EntityViewBody(props: EntityViewBodyProps) {
  const { spec, type, entities, invalid, users, onCreate, busy, conflicts, onDismissConflict, catalogDiagnostics, schemaMissing, viewConfig } =
    props;
  const canWrite = props.canWrite !== false; // omitted ≡ writable (§E)
  const renderer = resolveViewRenderer(spec.view);
  const { Component } = renderer;
  // #698 — the kind declares whether it draws entity records; the parser no
  // longer decides. A kind that needs one and wasn't given one is a MALFORMED
  // view, so say so — it used to fall out of the parser as `null` and silently
  // become a raw YAML tree, which reads as "this file is not a view".
  const missingEntity = !!renderer.needsEntity && !spec.entity;
  // Entity-shaped chrome keys off whether this VIEW names an entity, not off
  // whether the kind requires one. The container fetches from `spec.entity`
  // (AiYamlRenderer), so a kind without `needsEntity` whose file still names an
  // `entity:` does get records — gating on `needsEntity` suppressed the
  // schema-missing warning for exactly that view, and claimed in the docs that
  // its entity props were empty when they were not.
  const hasEntity = !!spec.entity;
  const showEmpty = hasEntity && entities.length === 0 && !renderer.ownsEmptyState;
  return (
    <div className="ev-panel">
      <div className="ev-panel__head">
        <h3 className="ev-panel__title">
          {spec.title || spec.entity || spec.view}
          {entities.length > 0 && <span className="ev-panel__count">{entities.length}</span>}
        </h3>
        <div className="ev-panel__actions">
          {viewConfig && <ViewSettingsPanel config={viewConfig} />}
          {type && !renderer.suppressQuickCreate && canWrite && (
            <QuickCreate
              form={type.form}
              users={users}
              onCreate={onCreate}
              busy={busy}
              entityLabel={type.name}
              refOptionsFor={(name) => refOptionsForField(type, props.refIndex, name)}
            />
          )}
        </div>
      </div>
      {conflicts && conflicts.length > 0 && <ConflictBanner conflicts={conflicts} onDismiss={onDismissConflict} />}
      {catalogDiagnostics && catalogDiagnostics.length > 0 && <DiagnosticBanner diagnostics={catalogDiagnostics} />}
      {/* Only a view that NAMES an entity can be missing that entity's schema.
          A kind drawing a workspace file names none, so this used to render as
          "No schema for  — showing raw fields", with an empty name, above a
          perfectly good view. */}
      {schemaMissing && hasEntity && (
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
      {missingEntity ? (
        <div role="status" className="ev-banner">
          <span className="ev-banner__icon" aria-hidden>
            ⚠
          </span>
          <div className="ev-banner__body">
            The <code>{spec.view}</code> view needs an <code>entity:</code> naming which records to draw.
          </div>
        </div>
      ) : showEmpty ? (
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
