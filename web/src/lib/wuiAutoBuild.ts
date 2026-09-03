/**
 * Per-page "rebuild this when I open it" toggle.
 *
 * A page with a build step has two halves — the `src/` someone edits and the
 * `dist/` a viewer sees — and nothing keeps them together. Rebuilding on open is
 * the only setting under which going stale is IMPOSSIBLE rather than merely
 * unlikely, so it is the default; the build's output is on screen while it runs,
 * so it is a visible cost rather than a mysterious pause.
 *
 * It is a choice, not a rule, because the cost is real: a build takes tens of
 * seconds and wakes the item's sandbox. Whoever finds that a bad trade for a
 * particular page turns it off there.
 *
 * PER PAGE and per viewer, in localStorage — the same shape as the chat's
 * sticky pickers. Per page because one page builds in two seconds and another
 * in sixty; per viewer because this is about how someone wants to open a thing,
 * not about how the page is configured.
 */
import { useCallback, useEffect, useState } from "react";

const KEY = "rca.wuiAutoBuild";

/** The identity of one page, for storage. Both halves matter: two items can
 * hold folders of the same name, and one item can hold many pages. */
export function autoBuildScope(itemId: string, folder: string): string {
  return `${itemId}:${folder}`;
}

export function getWuiAutoBuild(scope: string): boolean {
  try {
    // Anything other than the explicit opt-out reads as the default (on).
    return localStorage.getItem(`${KEY}.${scope}`) !== "off";
  } catch {
    return true;
  }
}

export function setWuiAutoBuild(scope: string, on: boolean): void {
  try {
    if (on) localStorage.removeItem(`${KEY}.${scope}`);
    else localStorage.setItem(`${KEY}.${scope}`, "off");
  } catch {
    /* localStorage unavailable — the pick just isn't sticky */
  }
}

/** React state bound to one page's sticky toggle.
 *
 * The state is DERIVED from the scope, not initialised from it: a pane that
 * moves from one page to another without unmounting would otherwise keep
 * showing — and acting on — the previous page's answer. */
export function useWuiAutoBuild(scope: string): readonly [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => getWuiAutoBuild(scope));
  useEffect(() => setOn(getWuiAutoBuild(scope)), [scope]);
  const set = useCallback(
    (value: boolean) => {
      setOn(value);
      setWuiAutoBuild(scope, value);
    },
    [scope],
  );
  return [on, set] as const;
}
