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
import { pxToRem } from "../../lib/pxToRem";
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
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>
          #{record.number} {String(record.fields.title ?? type.name)}
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
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {settable.map((f) => (
            <label key={f.name} style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: pxToRem(13) }}>
              <span style={{ color: "var(--text-paper-d)" }}>
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
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
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
          {yamlError && <div style={{ color: "var(--err)", fontSize: pxToRem(12) }}>{yamlError}</div>}
        </div>
      )}

      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: pxToRem(13) }}>
        <span style={{ color: "var(--text-paper-d)" }}>Body</span>
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
