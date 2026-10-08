/**
 * The viewer's clock for schedule times (`docs/plan-schedule-overview-polish.md`):
 * their zone, their language, and a `now` that moves — every 30 s, so "15 分鐘後"
 * becomes "14 分鐘後" without a refetch.
 *
 * `ViewerClockPin` fixes the zone and `now` for a test: the machine a test runs
 * on has a zone of its own, and a test that read it would pass in one place and
 * fail in another.
 */
import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { useLocale } from "./i18n";
import { viewerZone } from "./scheduleTime";

export type ViewerClock = { viewer: string; now: number; locale: string };

const Pin = createContext<{ viewer?: string; now?: number }>({});

export function ViewerClockPin({
  viewer,
  now,
  children,
}: {
  viewer: string;
  now: number;
  children: React.ReactNode;
}) {
  return <Pin.Provider value={{ viewer, now }}>{children}</Pin.Provider>;
}

const TICK_MS = 30000;

export function useViewerClock(): ViewerClock {
  const pin = useContext(Pin);
  const [locale] = useLocale();
  const [tick, setTick] = useState(() => Date.now());
  const pinned = pin.now !== undefined;
  useEffect(() => {
    if (pinned) return;
    const id = setInterval(() => setTick(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, [pinned]);
  return useMemo(
    () => ({ viewer: pin.viewer ?? viewerZone(), now: pin.now ?? tick, locale }),
    [pin.viewer, pin.now, tick, locale],
  );
}
