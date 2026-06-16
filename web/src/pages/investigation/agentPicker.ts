/**
 * Adapt an App's manifest agent picker (`{preset, name}[]`) to the composer's
 * ModelEffortPicker (#89 candidate 3). The picker offers the App's declared
 * presets by friendly `name`; the value persisted on the item is the `preset`
 * (`attached_preset`, which the backend AppCatalog resolves per turn). These
 * helpers bridge the display-name ↔ preset gap without changing ModelEffortPicker.
 */

import type { PickerEntry } from "../../components/ModelEffortPicker";

type AppPickerEntry = { preset: string; name: string };

/** ModelEffortPicker keys on `name`; show the friendly name, carry the preset. */
export function pickerModels(picker: AppPickerEntry[]): PickerEntry[] {
  return picker.map((p) => ({ name: p.name, model: p.preset }));
}

/** The display name for the currently-attached preset (the selected entry). */
export function nameForPreset(picker: AppPickerEntry[], preset: string): string | null {
  return picker.find((p) => p.preset === preset)?.name ?? null;
}

/** The preset to persist (`attached_preset`) when a display name is picked. */
export function presetForName(picker: AppPickerEntry[], name: string): string | null {
  return picker.find((p) => p.name === name)?.preset ?? null;
}
