import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * Breadcrumb trail shared by the global nav (#158). Each page *publishes* its
 * own trail via `useBreadcrumbs([...])` once its data (App title, item title,
 * doc name) has loaded; the global bar reads the latest trail via
 * `useBreadcrumbTrail()` and renders it. Decoupling render from data this way
 * keeps the trail app-agnostic and testable, and means the bar never has to
 * know how any page derives its labels.
 */
export type Crumb = { label: string; to?: string };

type BreadcrumbCtx = {
  crumbs: Crumb[];
  setCrumbs: (crumbs: Crumb[]) => void;
};

const BreadcrumbContext = createContext<BreadcrumbCtx | null>(null);

export function BreadcrumbProvider({ children }: { children: ReactNode }) {
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const value = useMemo(() => ({ crumbs, setCrumbs }), [crumbs]);
  return <BreadcrumbContext.Provider value={value}>{children}</BreadcrumbContext.Provider>;
}

/** Read the current trail (the global bar). Empty outside a provider. */
export function useBreadcrumbTrail(): Crumb[] {
  return useContext(BreadcrumbContext)?.crumbs ?? [];
}

/**
 * Publish this page's trail; the latest caller wins. No-ops gracefully outside
 * a provider so pages stay unit-testable in isolation. Keyed on the serialized
 * crumbs (not array identity) so a fresh array literal each render is fine.
 */
export function useBreadcrumbs(crumbs: Crumb[]): void {
  const setCrumbs = useContext(BreadcrumbContext)?.setCrumbs;
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    setCrumbs?.(crumbs);
    // `key` captures the crumb contents; re-run only when they actually change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setCrumbs]);
}
