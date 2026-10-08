import type { QueryClient } from "@tanstack/react-query";

/**
 * A workspace's skills changed — a skill hub copy went in, from wherever: the
 * skill page's dialog, the Skills panel's picker, the chat card, a fork. What
 * every skill page says about where it is installed (`…/installs`) and what
 * installing would do (`…/targets`) is then stale, whichever entry it is
 * about, so both are dropped by prefix (review round 1: only the dialog's own
 * install did this, and the rest showed stale state for the cache's life).
 */
export function invalidateHubInstalls(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ["skillHub", "installs"] });
  void qc.invalidateQueries({ queryKey: ["skillHub", "targets"] });
}
