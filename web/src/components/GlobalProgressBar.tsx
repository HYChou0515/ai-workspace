import { useEffect, useState } from "react";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";

/**
 * A thin top-edge progress bar that signals "the app is talking to the
 * backend" for ANY in-flight request — every `useQuery` fetch and every
 * `useMutation`, across collection / chat / and every other page (issue #151).
 *
 * Wired to TanStack Query's global counters so it needs zero per-call wiring;
 * one mount in `App` covers the whole app and any future request automatically.
 *
 * Debounced: a request must stay in flight past `DEBOUNCE_MS` before the bar
 * appears, so cache hits and sub-150ms round-trips don't flash a bar on every
 * page switch (which reads as a glitch, not as loading).
 */
const DEBOUNCE_MS = 150;

export function GlobalProgressBar() {
  const active = useIsFetching() + useIsMutating() > 0;
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!active) {
      setVisible(false);
      return;
    }
    const timer = setTimeout(() => setVisible(true), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [active]);

  if (!visible) return null;
  return (
    <div className="global-progress" role="progressbar" aria-label="載入中" aria-busy="true">
      <div className="global-progress__bar" />
    </div>
  );
}
