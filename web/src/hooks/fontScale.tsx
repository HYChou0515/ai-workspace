/**
 * System font size (#226). A single global scale drives the document root font
 * size as a percentage — `:root { font-size: scale*100% }` — so every `rem`
 * (the --text-* tokens and pxToRem() call sites) grows or shrinks with it.
 * Percentage (not a fixed px) keeps the browser/OS base honoured. Spacing px is
 * deliberately left fixed, so multi-pane layouts keep their width while only
 * text scales — unlike browser/CSS zoom, which shrinks the viewport and breaks
 * the columns.
 *
 * The scale is shared via context (FontScaleProvider) so a change from the
 * Settings slider live-updates every consumer — including Monaco, whose font
 * size is a JS option (not a CSS rem) and must re-render to pick up the change
 * (see useMonacoFontSize). Choice persists in localStorage and is applied once
 * at startup (initFontScale) before render so there's no flash.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

const KEY = "ui:font-scale";

export const FONT_SCALE_MIN = 0.85;
export const FONT_SCALE_MAX = 1.5;
export const FONT_SCALE_DEFAULT = 1;
/** Slider granularity — 5% steps. */
export const FONT_SCALE_STEP = 0.05;

export function clampFontScale(scale: number): number {
  if (!Number.isFinite(scale)) return FONT_SCALE_DEFAULT;
  return Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, scale));
}

export function readFontScale(): number {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw == null) return FONT_SCALE_DEFAULT;
    return clampFontScale(Number.parseFloat(raw));
  } catch {
    return FONT_SCALE_DEFAULT;
  }
}

function apply(scale: number): void {
  // toFixed strips float artefacts (1.1 * 100 = 110.00000000000001).
  const pct = +(clampFontScale(scale) * 100).toFixed(2);
  document.documentElement.style.fontSize = `${pct}%`;
}

/** Call once at startup (main.tsx) to apply the stored scale before render. */
export function initFontScale(): void {
  apply(readFontScale());
}

type FontScaleCtx = { scale: number; setScale: (scale: number) => void };

// Default (no provider) renders at the default scale with a no-op setter — like
// LocaleContext, so a component in isolation never crashes.
const FontScaleContext = createContext<FontScaleCtx>({
  scale: FONT_SCALE_DEFAULT,
  setScale: () => {},
});

export function FontScaleProvider({ children }: { children: ReactNode }) {
  const [scale, setScaleState] = useState<number>(readFontScale);
  useEffect(() => {
    apply(scale);
  }, [scale]);
  const setScale = useCallback((next: number) => {
    const clamped = clampFontScale(next);
    try {
      localStorage.setItem(KEY, String(clamped));
    } catch {
      /* ignore quota / privacy-mode errors */
    }
    setScaleState(clamped);
  }, []);
  const value = useMemo(() => ({ scale, setScale }), [scale, setScale]);
  return <FontScaleContext.Provider value={value}>{children}</FontScaleContext.Provider>;
}

export function useFontScale(): [number, (scale: number) => void] {
  const { scale, setScale } = useContext(FontScaleContext);
  return [scale, setScale];
}

/**
 * Monaco sets its font size as a JS option in px, ignoring the root rem, so it
 * needs the scale applied explicitly and must re-render when it changes.
 */
export function useMonacoFontSize(basePx: number): number {
  const [scale] = useFontScale();
  return Math.round(basePx * scale);
}
