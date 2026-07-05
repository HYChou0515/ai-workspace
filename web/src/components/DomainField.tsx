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

  const open = onChange ? () => setEditing(true) : undefined;
  if (field.kind === "select") {
    return (
      <span
        data-tone={tone}
        style={chipStyle(tone)}
        onClick={open}
        role={onChange ? "button" : undefined}
      >
        {text}
      </span>
    );
  }
  return <span onClick={open}>{text}</span>;
}
