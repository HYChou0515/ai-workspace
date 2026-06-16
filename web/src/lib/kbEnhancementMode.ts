/** Per-message enhancement mode for the KB chat composer
 * (replaces the legacy `kbQuick` single-bool toggle).
 *
 * UX (Phase C): a Mode dropdown — `quick` / `standard` / `thorough`
 * — plus an Advanced disclosure with three sliders (`expand`, `hyde`,
 * `rerank`). Selecting a mode autofills the sliders to the canonical
 * preset for that mode; adjusting a slider flips the mode label to
 * "custom" so the user can see they've deviated.
 *
 * Translation to the BE's `body.enhancements`:
 *   - quick     → { expand: 0, hyde: 0, rerank: false }
 *   - standard  → undefined  (BE uses operator default — sends no payload)
 *   - thorough  → { expand: 99, hyde: 99, rerank: true } (BE clamps to operator max)
 *   - custom    → whatever the sliders show, verbatim
 *
 * Sticky in localStorage so the picker survives a reload.
 */
import { useCallback, useState } from "react";

export type EnhancementMode = "quick" | "standard" | "thorough" | "custom";

export type CustomEnhancements = {
  expand: number;
  hyde: number;
  rerank: boolean;
};

export type EnhancementSelection = {
  mode: EnhancementMode;
  /** Only set when `mode === "custom"`. Sliders write here. */
  custom?: CustomEnhancements;
};

export type BodyEnhancements = {
  expand?: number | null;
  hyde?: number | null;
  rerank?: boolean | null;
  /** Issue #50 P6: route this query through the LLM wiki ("Search the wiki").
   * Separate from the depth dials — it picks a retrieval path, not a knob. */
  wiki?: boolean | null;
};

/** The values the sliders snap to when the user picks a Mode. */
export const PRESETS: Record<Exclude<EnhancementMode, "custom">, CustomEnhancements> = {
  quick: { expand: 0, hyde: 0, rerank: false },
  // "standard" means "inherit operator defaults" — sliders show "1, 0, on"
  // which matches the shipped bundled defaults (see plan-kb-retrieval-
  // enhancements.md). The BE distinguishes these from explicit user
  // values by sending NO `enhancements` payload at all (see toBody).
  standard: { expand: 1, hyde: 0, rerank: true },
  thorough: { expand: 99, hyde: 99, rerank: true },
};

/** Translate the FE selection into the JSON the BE expects. `standard`
 * means "inherit", expressed by sending NO payload at all. */
export function toBodyEnhancements(sel: EnhancementSelection): BodyEnhancements | undefined {
  if (sel.mode === "standard") return undefined;
  const v = sel.mode === "custom" ? sel.custom : PRESETS[sel.mode];
  if (!v) return undefined;
  return { expand: v.expand, hyde: v.hyde, rerank: v.rerank };
}

/** Fold the per-query "Search the wiki" toggle into the depth body. The wiki
 * flag is a separate sticky bool (it picks a retrieval PATH, not a depth dial),
 * so it can ride on top of any depth mode — including "standard" (which sends
 * no depth payload): wiki-on alone still produces `{ wiki: true }`. */
export function withWikiFlag(
  body: BodyEnhancements | undefined,
  wiki: boolean,
): BodyEnhancements | undefined {
  if (!wiki) return body;
  return { ...(body ?? {}), wiki: true };
}

const WIKI_KEY = "rca.kbSearchWiki";

export function getKbWiki(): boolean {
  try {
    return localStorage.getItem(WIKI_KEY) === "1";
  } catch {
    return false;
  }
}

export function setKbWiki(on: boolean): void {
  try {
    localStorage.setItem(WIKI_KEY, on ? "1" : "0");
  } catch {
    /* localStorage unavailable — the toggle just isn't sticky */
  }
}

/** React state bound to the sticky "Search the wiki" toggle. */
export function useKbWikiToggle(): readonly [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(getKbWiki);
  const set = useCallback((v: boolean) => {
    setOn(v);
    setKbWiki(v);
  }, []);
  return [on, set] as const;
}

const KEY = "rca.kbEnhancementMode";

/** Persist the user's last selection so the composer's Mode dropdown
 * + slider positions survive a reload. Custom values are stored too
 * so re-opening the chat keeps the dial positions the user set. */
export function getStored(): EnhancementSelection {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { mode: "standard" };
    const parsed = JSON.parse(raw) as Partial<EnhancementSelection>;
    if (
      parsed.mode === "quick" ||
      parsed.mode === "standard" ||
      parsed.mode === "thorough" ||
      parsed.mode === "custom"
    ) {
      return {
        mode: parsed.mode,
        custom:
          parsed.mode === "custom" && parsed.custom
            ? {
                expand: Math.max(0, Math.floor(parsed.custom.expand ?? 0)),
                hyde: Math.max(0, Math.floor(parsed.custom.hyde ?? 0)),
                rerank: !!parsed.custom.rerank,
              }
            : undefined,
      };
    }
    return { mode: "standard" };
  } catch {
    return { mode: "standard" };
  }
}

export function setStored(sel: EnhancementSelection): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(sel));
  } catch {
    /* localStorage unavailable (private mode / SSR) — selection just isn't sticky */
  }
}

/** React state bound to the sticky selection. Returns `[selection,
 * setMode, setSlider]` — `setMode` flips to a preset; `setSlider`
 * edits one knob and auto-flips mode to "custom" if the new value
 * differs from the active preset. */
export function useKbEnhancementMode(): readonly [
  EnhancementSelection,
  (mode: EnhancementMode) => void,
  (knob: keyof CustomEnhancements, value: number | boolean) => void,
] {
  const [sel, setSel] = useState<EnhancementSelection>(getStored);
  const setMode = useCallback((mode: EnhancementMode) => {
    const next: EnhancementSelection = { mode };
    if (mode === "custom") {
      // First flip to custom captures the current preset's values so
      // sliders have something to show. Later edits replace these.
      next.custom = PRESETS[sel.mode === "custom" ? "standard" : sel.mode];
    }
    setSel(next);
    setStored(next);
  }, [sel.mode]);
  const setSlider = useCallback(
    (knob: keyof CustomEnhancements, value: number | boolean) => {
      const base: CustomEnhancements =
        sel.mode === "custom" && sel.custom
          ? sel.custom
          : PRESETS[sel.mode === "custom" ? "standard" : sel.mode];
      const updated: CustomEnhancements = { ...base, [knob]: value };
      // If the result equals an exact preset, snap the mode to that
      // preset so the dropdown label updates.
      const next: EnhancementSelection = matchesPreset(updated) ?? {
        mode: "custom",
        custom: updated,
      };
      setSel(next);
      setStored(next);
    },
    [sel.mode, sel.custom],
  );
  return [sel, setMode, setSlider] as const;
}

/** When the current slider values exactly equal a Mode preset, return
 * `{mode}` for that preset so the dropdown can re-snap. Otherwise
 * null. */
function matchesPreset(v: CustomEnhancements): EnhancementSelection | null {
  for (const mode of ["quick", "standard", "thorough"] as const) {
    const p = PRESETS[mode];
    if (p.expand === v.expand && p.hyde === v.hyde && p.rerank === v.rerank) {
      return { mode };
    }
  }
  return null;
}
