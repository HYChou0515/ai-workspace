/**
 * Per-page "rebuild this when I open it" toggle.
 *
 * A page with a build step has two halves — the `src/` someone edits and the
 * `dist/` a viewer sees — and nothing keeps them together. Rebuilding on open is
 * the setting that closes that gap for everyone who opens the page, so it is the
 * default; the build's output is on screen while it runs, so it is a visible
 * cost rather than a mysterious pause.
 *
 * It is not a GUARANTEE, and the code does not claim to be one: the manifest
 * read that decides a page has a build is allowed to fail quietly (a page
 * without one is the ordinary case), and a build that fails leaves the previous
 * `dist/` up. Both leave a reader on an older page — with, in the second case,
 * the failure on screen above it.
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
import { useCallback, useState } from "react";

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
 * showing — and acting on — the previous page's answer.
 *
 * Derived DURING RENDER, not in an effect. An effect runs after the render that
 * changed the scope, and other effects run before it — so on that one render
 * the answer still belonged to the page just left, and the pane acted on it:
 * arriving at a page whose switch is off rebuilt it anyway, taking the page
 * away for the length of a build and waking the sandbox, which is the exact
 * cost the switch exists to decline. (React's documented way to adjust state
 * when a prop changes: set it during render, and the re-render happens before
 * anything else sees the stale value.) */
export function useWuiAutoBuild(scope: string): readonly [boolean, (on: boolean) => void] {
  const [state, setState] = useState(() => ({ scope, on: getWuiAutoBuild(scope) }));
  // The stored state is only trusted for the scope it was stored FOR; for any
  // other, the answer comes straight from storage on this very render. No
  // effect, no extra render, and nothing that can be one render behind.
  const on = state.scope === scope ? state.on : getWuiAutoBuild(scope);
  const setOn = (value: boolean) => setState({ scope, on: value });
  const set = useCallback(
    (value: boolean) => {
      setOn(value);
      setWuiAutoBuild(scope, value);
    },
    [scope],
  );
  return [on, set] as const;
}
