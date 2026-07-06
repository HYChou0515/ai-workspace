/**
 * EntityFileEditor (#453, §C2) — the single-entity editing surface. A record is
 * a file (frontmatter + markdown body), so this is a *file* editor, not a
 * hand-rolled form: the frontmatter edits as a role-widget form OR as raw YAML
 * (the self-heal escape hatch), and the body is free markdown. Save rides the
 * one write path (`useEntityWrite.save` → the update route with a body, §B1/§B2),
 * so it inherits the optimistic + 409-conflict contract. Pure/presentational —
 * the container resolves the record + write handler and passes them in.
 *
 * Compute-on-read fields (backref / rollup) are never editable, so they're left
 * out of the form + the settable patch entirely.
 */

import { useState } from "react";

import { dump, load } from "js-yaml";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { MonacoEditor } from "../../components/MonacoEditor";
import { RoleField, widgetForRole } from "./roleWidget";

export type EntityFileEditorProps = {
  type: EntityType;
  record: EntityInstance;
  users?: User[];
  canWrite?: boolean;
  busy?: boolean;
  onSave: (patch: Record<string, unknown>, body: string) => void;
};

function settableFields(type: EntityType) {
  return type.fields.filter((f) => widgetForRole(f.role) !== "readonly");
}

function pickSettable(fields: Record<string, unknown>, type: EntityType): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of settableFields(type)) out[f.name] = fields[f.name];
  return out;
}

export function EntityFileEditor({ type, record, users, canWrite = true, busy, onSave }: EntityFileEditorProps) {
  const settable = settableFields(type);
  const [fields, setFields] = useState<Record<string, unknown>>(() => pickSettable(record.fields, type));
  const [body, setBody] = useState<string>(record.body ?? "");
  const [mode, setMode] = useState<"form" | "yaml">("form");
  const [yamlText, setYamlText] = useState("");
  const [yamlError, setYamlError] = useState<string | null>(null);

  const openYaml = () => {
    setYamlText(dump(fields));
    setYamlError(null);
    setMode("yaml");
  };

  const onYamlChange = (text: string) => {
    setYamlText(text);
    let parsed: unknown;
    try {
      parsed = load(text);
    } catch {
      setYamlError("Invalid YAML — fix it before saving.");
      return;
    }
    if (parsed === null || parsed === undefined) {
      setFields({});
      setYamlError(null);
    } else if (typeof parsed === "object" && !Array.isArray(parsed)) {
      setFields(parsed as Record<string, unknown>);
      setYamlError(null);
    } else {
      setYamlError("Frontmatter must be a mapping of fields.");
    }
  };

  const blocked = !canWrite || !!busy || (mode === "yaml" && yamlError !== null);
  const save = () => {
    if (blocked) return;
    onSave(fields, body);
  };

  return (
    <div className="ev-editor">
      <div className="ev-editor__head">
        <h3 className="ev-editor__title">
          <span className="ev-editor__title-num">#{record.number}</span>
          {String(record.fields.title ?? type.name)}
        </h3>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            data-size="sm"
            onClick={() => (mode === "form" ? openYaml() : setMode("form"))}
          >
            {mode === "form" ? "Raw YAML" : "Form"}
          </button>
          <button type="button" className="btn" data-variant="primary" data-size="sm" disabled={blocked} onClick={save}>
            Save
          </button>
        </div>
      </div>

      {mode === "form" ? (
        <div className="ev-editor__form">
          {settable.map((f) => (
            <label key={f.name} className="ev-editor__field">
              <span className="ev-editor__label">
                {f.name}
                {f.required ? " *" : ""}
              </span>
              <RoleField
                widget={widgetForRole(f.role)}
                name={f.name}
                value={fields[f.name]}
                values={f.values}
                users={users}
                disabled={!canWrite}
                onCommit={(v) => setFields((s) => ({ ...s, [f.name]: v }))}
              />
            </label>
          ))}
        </div>
      ) : (
        <div className="ev-editor__field">
          {/* Raw-YAML frontmatter rides the same Monaco stack as the rest of the
              IDE — the escape hatch for fields the widgets can't express. */}
          <MonacoEditor
            ariaLabel="frontmatter yaml"
            value={yamlText}
            onChange={onYamlChange}
            language="yaml"
            readOnly={!canWrite}
            autoHeight
            minHeight={120}
          />
          {yamlError && (
            <div role="alert" className="ev-banner ev-banner--err">
              <span className="ev-banner__icon" aria-hidden>
                ⚠
              </span>
              <div className="ev-banner__body">{yamlError}</div>
            </div>
          )}
        </div>
      )}

      <label className="ev-editor__field">
        <span className="ev-editor__label">Body</span>
        {/* Free-writing markdown body in the shared Monaco editor (§C2). */}
        <MonacoEditor
          ariaLabel="body"
          value={body}
          onChange={setBody}
          language="markdown"
          readOnly={!canWrite}
          autoHeight
          minHeight={160}
        />
      </label>
    </div>
  );
}
