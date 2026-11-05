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
/** Agent Hub's own choice (plan-wui-publish-and-agent-hub A7): the same
 * layout, a separate place — so a separate memory of how it is looked at. */
export const AGENT_HUB_VIEW = "rca.agentHubView";

export type WuiView = "cards" | "table";

export function readWuiView(key: string = KEY): WuiView {
  try {
    return localStorage.getItem(key) === "table" ? "table" : "cards";
  } catch {
    return "cards";
  }
}

export function writeWuiView(view: WuiView, key: string = KEY): void {
  try {
    localStorage.setItem(key, view);
  } catch {
    /* localStorage unavailable — the choice just isn't sticky */
  }
}

/** React state over the choice: initialised from storage, every set written
 * through. */
export function useWuiView(key: string = KEY): [WuiView, (view: WuiView) => void] {
  const [view, setState] = useState<WuiView>(() => readWuiView(key));
  const set = useCallback(
    (next: WuiView) => {
      writeWuiView(next, key);
      setState(next);
    },
    [key],
  );
  return [view, set];
}
