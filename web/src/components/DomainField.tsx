/**
 * Renders one of a WorkItem's domain fields from its {@link FieldSpec} (#89
 * P7b) — the App-agnostic replacement for RCA's hardcoded SeverityChip/StatusChip
 * in the shell's breadcrumb / statusbar / footer.
 *
 * Resting state: a `select` field shows as a toned chip (tone resolved by the
 * caller from the App's `field_styles`); a `text` field shows its value plainly.
 * When `onChange` is given the field is inline-editable: clicking the resting
 * value opens an editor (a dropdown for `select`, an input for `text`) that
 * commits the new value via `onChange`. Without `onChange` it stays read-only.
 */

import { useState } from "react";

import type { FieldSpec } from "../api/types";
import { Icon } from "./Icon";
import { type ChipTone, chipStyle } from "./StatusChip";

export function DomainField({
  field,
  value,
  tone = "muted",
  onChange,
}: {
  field: FieldSpec;
  value: unknown;
  tone?: ChipTone;
  onChange?: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const text = value == null ? "" : String(value);

  if (editing && field.kind === "select") {
    return (
      <select
        className="inline-edit"
        autoFocus
        defaultValue={text}
        onChange={(e) => {
          onChange?.(e.target.value);
          setEditing(false);
        }}
        onBlur={() => setEditing(false)}
      >
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  if (editing && field.kind === "text") {
    return (
      <input
        className="inline-edit"
        autoFocus
        defaultValue={text}
        onBlur={(e) => {
          onChange?.(e.target.value);
          setEditing(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    );
  }

  // Editable → a REAL <button> (keyboard-operable, cursor, focus) with a pencil
  // hint, so an inline-editable value never masquerades as a passive chip/label
  // (#466 ①). Read-only → a plain, non-interactive <span>.
  const open = onChange ? () => setEditing(true) : undefined;
  if (field.kind === "select") {
    if (!open) {
      return (
        <span data-tone={tone} style={chipStyle(tone)}>
          {text}
        </span>
      );
    }
    return (
      <button
        type="button"
        data-tone={tone}
        onClick={open}
        title="Click to edit"
        style={{ ...chipStyle(tone), border: "none", cursor: "pointer" }}
      >
        {text}
        <Icon name="pencil" size={9} />
      </button>
    );
  }
  if (!open) return <span>{text}</span>;
  return (
    <button
      type="button"
      onClick={open}
      title="Click to edit"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: 0,
        font: "inherit",
        color: "inherit",
        background: "none",
        border: "none",
        borderBottom: "1px dashed var(--paper-3)",
        cursor: "pointer",
      }}
    >
      {text || "—"}
      <Icon name="pencil" size={9} color="var(--text-paper-d2)" />
    </button>
  );
}
