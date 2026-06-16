/**
 * Renders an App item's domain fields for one shell surface (#89 P7b), driven by
 * `manifest.layout[surface] × manifest.fields` — the App-agnostic replacement
 * for the shell's hardcoded RCA breadcrumb/statusbar/footer chips. Each layout
 * field name is looked up in the field schema and rendered via {@link DomainField};
 * a `select` field's chip tone comes from the App's `field_styles` overlay. A
 * layout name with no matching FieldSpec is skipped.
 */

import type { AppItem, AppManifest } from "../api/types";
import { DomainField } from "./DomainField";

type Surface = "breadcrumb" | "statusbar" | "form";

export function DomainFields({
  surface,
  manifest,
  item,
  onEditField,
}: {
  surface: Surface;
  manifest: AppManifest;
  item: AppItem;
  /** When given, each field is inline-editable; committing calls this with the
   * field name + new value (the shell's `useUpdateItemField` setter). */
  onEditField?: (name: string, value: string) => void;
}) {
  const byName = new Map(manifest.fields.map((f) => [f.name, f]));
  const names = manifest.layout[surface] ?? [];
  return (
    <>
      {names.map((name) => {
        const field = byName.get(name);
        if (!field) return null;
        const value = item[name];
        const tone = manifest.field_styles?.[name]?.[String(value)];
        return (
          <DomainField
            key={name}
            field={field}
            value={value}
            tone={tone}
            onChange={onEditField ? (v) => onEditField(name, v) : undefined}
          />
        );
      })}
    </>
  );
}
