import type { ItemToolState, ToolPref } from "../api/types";

/**
 * The tool picker's folds (plan-tools-picker-groups). Pure: the component
 * renders whatever this says, and the tests pin the rules here.
 *
 * A fold is every row whose server-named `group` matches — built-ins all say
 * `"builtin"`, a package's commands and its whole-package row say the package
 * id. The FOLD IS THE CEILING: it holds exactly the rows `app.json` granted,
 * never the package's full command list, so a package the app granted two
 * commands of is a two-row fold that reads On when both are on. Nothing here
 * ever asks what else the package could do.
 */
export type ToolGroup = {
  /** The server's fold key (`"builtin"` or the raw package id). */
  id: string;
  /** What the header shows. `builtin` is literally `builtin`; a package fold
   * takes the label its rows already carry (`package` on a command row, the
   * row's own label on a whole-package row). */
  label: string;
  tools: ItemToolState[];
};

export const BUILTIN_GROUP = "builtin";

/** Fold `tools` by `group`: the builtin fold first, then each package fold in
 * the order its first row appears; rows keep their order inside a fold. */
export function groupsOf(tools: ItemToolState[]): ToolGroup[] {
  const byId = new Map<string, ToolGroup>();
  for (const tool of tools) {
    let g = byId.get(tool.group);
    if (!g) {
      g = { id: tool.group, label: labelOf(tool), tools: [] };
      byId.set(tool.group, g);
    }
    g.tools.push(tool);
  }
  const groups = [...byId.values()];
  const i = groups.findIndex((g) => g.id === BUILTIN_GROUP);
  if (i > 0) groups.unshift(...groups.splice(i, 1));
  return groups;
}

function labelOf(tool: ItemToolState): string {
  if (tool.group === BUILTIN_GROUP) return BUILTIN_GROUP;
  return tool.package ?? tool.label;
}

export type GroupState = ToolPref | "mixed";

/** The fold's derived state: the one state every row shares, or `mixed`. It
 * is never stored — the override map only knows rows. */
export function groupState(group: ToolGroup, prefs: Record<string, boolean>): GroupState {
  let seen: ToolPref | null = null;
  for (const tool of group.tools) {
    const s = prefOf(tool.key, prefs);
    if (seen === null) seen = s;
    else if (seen !== s) return "mixed";
  }
  return seen ?? "follow";
}

/** The override with every row of the fold set to `next` (`follow` = removed). */
export function withGroupState(
  group: ToolGroup,
  prefs: Record<string, boolean>,
  next: ToolPref,
): Record<string, boolean> {
  const out = { ...prefs };
  for (const tool of group.tools) {
    if (next === "follow") delete out[tool.key];
    else out[tool.key] = next === "on";
  }
  return out;
}

export function prefOf(key: string, prefs: Record<string, boolean>): ToolPref {
  return key in prefs ? (prefs[key] ? "on" : "off") : "follow";
}
