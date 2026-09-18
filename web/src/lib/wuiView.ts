/**
 * Which way the WUI overview is drawn — cards or the table
 * (`docs/plan-wui-overview-icon-favourites.md`, the cards amendment).
 *
 * Remembered PER BROWSER in localStorage, the `wuiAutoBuild` shape: this is a
 * convenience about how someone likes to look at a list, not an identity like
 * the favourites, so it is not scoped by user. Cards is the default and the
 * answer for anything that is not one of the two words. Both sides in
 * try/catch — a storage that throws makes the choice not sticky, not the page
 * broken.
 */
import { useCallback, useState } from "react";

const KEY = "rca.wuiView";

export type WuiView = "cards" | "table";

export function readWuiView(): WuiView {
  try {
    return localStorage.getItem(KEY) === "table" ? "table" : "cards";
  } catch {
    return "cards";
  }
}

export function writeWuiView(view: WuiView): void {
  try {
    localStorage.setItem(KEY, view);
  } catch {
    /* localStorage unavailable — the choice just isn't sticky */
  }
}

/** React state over the choice: initialised from storage, every set written
 * through. */
export function useWuiView(): [WuiView, (view: WuiView) => void] {
  const [view, setState] = useState<WuiView>(readWuiView);
  const set = useCallback((next: WuiView) => {
    writeWuiView(next);
    setState(next);
  }, []);
  return [view, set];
}
