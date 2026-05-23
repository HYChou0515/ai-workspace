/**
 * Fishbone (.canvas) schema. Schema is an agent-side convention — the BE
 * has zero awareness. See contract.md §5.
 */

export type FishboneSide = "top" | "bot";

export const SIX_M = [
  "Machine",
  "Method",
  "Material",
  "Man",
  "Measurement",
  "Environment",
] as const;
export type SixM = (typeof SIX_M)[number];

export type FishboneItem = { t: string; strong?: boolean };
export type FishboneBranch = {
  label: SixM;
  side: FishboneSide;
  items: FishboneItem[];
};

export type Fishbone = {
  effect: string;
  branches: FishboneBranch[];
};

/**
 * Parse a JSON string. Returns `null` if the shape doesn't match — the
 * caller falls back to a raw JSON renderer.
 */
export function parseFishbone(text: string): Fishbone | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (typeof obj.effect !== "string") return null;
  if (!Array.isArray(obj.branches)) return null;
  const branches: FishboneBranch[] = [];
  for (const b of obj.branches as unknown[]) {
    if (!b || typeof b !== "object") return null;
    const br = b as Record<string, unknown>;
    if (!SIX_M.includes(br.label as SixM)) return null;
    if (br.side !== "top" && br.side !== "bot") return null;
    if (!Array.isArray(br.items)) return null;
    const items: FishboneItem[] = [];
    for (const it of br.items as unknown[]) {
      if (!it || typeof it !== "object") return null;
      const item = it as Record<string, unknown>;
      if (typeof item.t !== "string") return null;
      items.push({ t: item.t, strong: item.strong === true });
    }
    branches.push({
      label: br.label as SixM,
      side: br.side,
      items,
    });
  }
  return { effect: obj.effect, branches };
}
