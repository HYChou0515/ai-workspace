/**
 * Theme: light / dark / system, applied via <html data-theme>. `system`
 * follows the OS via prefers-color-scheme. Choice persists in
 * localStorage and is applied once at startup (initTheme) so there's no
 * flash, plus a hook for the Settings picker.
 */

import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "system" | "light" | "dark";

const KEY = "rca:theme";
const mql = () => window.matchMedia("(prefers-color-scheme: dark)");

function resolve(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") return mql().matches ? "dark" : "light";
  return mode;
}

function apply(mode: ThemeMode): void {
  document.documentElement.dataset.theme = resolve(mode);
}

export function readThemeMode(): ThemeMode {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}

/** Call once at startup (main.tsx) to apply the stored theme + keep the
 * `system` choice live as the OS preference changes. */
export function initTheme(): void {
  apply(readThemeMode());
  mql().addEventListener("change", () => {
    if (readThemeMode() === "system") apply("system");
  });
}

export function useThemeMode(): [ThemeMode, (m: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(readThemeMode);
  useEffect(() => {
    apply(mode);
  }, [mode]);
  const set = useCallback((m: ThemeMode) => {
    try {
      localStorage.setItem(KEY, m);
    } catch {
      /* ignore */
    }
    setMode(m);
  }, []);
  return [mode, set];
}
