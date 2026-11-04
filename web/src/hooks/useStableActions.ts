import { useMemo, useRef } from "react";

type AnyFn = (...args: never[]) => unknown;

/**
 * `impl`'s functions behind proxies whose identity holds across renders: each
 * proxy calls the LATEST `impl` at call time. The object is rebuilt only when
 * the set of present actions changes (an action offered or withdrawn), which is
 * when a row has a different affordance to draw.
 *
 * Only for functions called LATER — on a click, a submit. A function called
 * while drawing (a URL resolver) must stay an ordinary prop: behind a proxy its
 * identity never changes, so a memo'd child never learns its answer did.
 */
export function useStableActions<T extends Record<string, AnyFn | undefined>>(impl: T): T {
  const latest = useRef(impl);
  latest.current = impl;
  const present = Object.keys(impl)
    .filter((k) => impl[k] !== undefined)
    .sort()
    .join(",");
  return useMemo(() => {
    const out: Record<string, AnyFn> = {};
    for (const key of present ? present.split(",") : []) {
      out[key] = (...args: never[]) => (latest.current[key] as AnyFn)(...args);
    }
    return out as T;
  }, [present]);
}
