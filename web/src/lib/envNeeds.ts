/**
 * What the item's current toolset says it wants from the environment (#750).
 *
 * The panel renders this two ways at once — a dropdown of the tools that
 * declared, and the fields of whichever is picked — and both are derived here
 * so they cannot drift apart. Storage is
 * untouched: this describes the SAME flat `Record<string, string>` the text box
 * edits, which is why a variable two tools want is one field with one value.
 *
 * A hint, never a gate. Nothing here refuses anything: a tool still receives
 * every variable the item holds, whether or not it said so.
 */
import type { ItemToolState } from "../api/types";
import { unstorable } from "./envFile";

export type EnvField = {
  name: string;
  description: string;
  required: boolean | null;
  /** Every tool asking for this name, by label. Shown beside the field so
   * clearing it is an informed act — the storage is flat, so a variable
   * emptied while one tool is showing is emptied for all of them. */
  wantedBy: string[];
  filled: boolean;
};

export type ToolEnvGroup = {
  key: string;
  label: string;
  fields: EnvField[];
  /** Who published it and which release resolved, for a third-party tool
   * (#724). Carried into the picker because two bundles can share a name and
   * differ only in who shipped them, and picking the wrong one is not an error
   * anything downstream can catch. `null` for a first-party tool. */
  author: string | null;
  version: string | null;
  /** How many of THIS tool's variables are marked required and still unset —
   * the number someone scanning a long list is actually looking for. Counts
   * only what an author marked required, like `missingRequired`. */
  missing: number;
};

export type EnvNeedsView = {
  /** One per effective tool that declared at least one variable — the rows
   * the picker offers, in the order the toolset resolved. */
  groups: ToolEnvGroup[];
  /** Labels of effective tools that shipped no declaration. Named, not
   * counted as needing nothing: almost every tool predates #750, and someone
   * hunting a missing variable must not be told there is nothing to find. */
  undeclared: string[];
  /** Names still to fill, counting ONLY variables an author explicitly marked
   * required. An unmarked variable is not "optional" — it is unstated — and
   * treating unstated as required makes a panel that always complains, which
   * is a panel nobody reads.
   *
   * Rendered as the panel's summary line, which is the whole reason this
   * feature exists: "I do not know what I am still missing". When it is empty
   * the panel says every variable MARKED required is set — deliberately not
   * "you are ready", because a tool that declared nothing, or an author who
   * left a name off, is invisible to this list. */
  missingRequired: string[];
};

export function deriveEnvNeeds(
  tools: ItemToolState[],
  values: Record<string, string>,
): EnvNeedsView {
  // Only what this item actually runs. A tool switched off has no bearing on
  // what is missing, and listing it would ask for variables nothing will read.
  const live = tools.filter((t) => t.effective);

  // A declaration is written by hand, by a third party, and a name this text
  // format cannot carry would give someone a field that quietly writes a
  // DIFFERENT variable (`A=B=v` reads back as A="B=v"). Offering that is worse
  // than offering nothing, so it is left out — which gates no tool, only an
  // input that could never have worked.
  const usable = (t: ItemToolState) => {
    const seen = new Set<string>();
    return (t.env_needs ?? []).filter((n) => {
      if (unstorable({ [n.name]: "x" }).length > 0) return false;
      // First mention wins. A declaration is hand-written, and the same name
      // twice in one file is a pasted line — which would otherwise put TWO
      // inputs on screen behind ONE stored value, so filling the second
      // silently discards what was typed in the first. Exactly the fault the
      // shared-variable design avoids across tools; it has to hold inside one
      // as well.
      if (seen.has(n.name)) return false;
      seen.add(n.name);
      return true;
    });
  };

  const wantedBy = new Map<string, string[]>();
  for (const t of live) {
    for (const need of usable(t)) {
      wantedBy.set(need.name, [...(wantedBy.get(need.name) ?? []), t.label]);
    }
  }

  const filled = (name: string) => (values[name] ?? "").trim() !== "";

  const groups: ToolEnvGroup[] = live
    .filter((t) => t.env_needs && usable(t).length > 0)
    .map((t) => ({
      key: t.key,
      label: t.label,
      author: t.author ?? null,
      version: t.version ?? null,
      missing: usable(t).filter((n) => n.required === true && !filled(n.name)).length,
      fields: usable(t).map((need) => ({
        name: need.name,
        description: need.description,
        required: need.required ?? null,
        wantedBy: wantedBy.get(need.name) ?? [t.label],
        filled: filled(need.name),
      })),
    }));

  const missingRequired = [
    ...new Set(
      live.flatMap((t) =>
        usable(t)
          .filter((n) => n.required === true && !filled(n.name))
          .map((n) => n.name),
      ),
    ),
  ];

  return {
    groups,
    // `null` only. A tool answering `[]` looked and needs nothing — that is a
    // claim, and repeating it as a caveat would bury the tools that made none.
    undeclared: live.filter((t) => t.env_needs == null).map((t) => t.label),
    missingRequired,
  };
}
