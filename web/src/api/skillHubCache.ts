import type { QueryClient } from "@tanstack/react-query";

/**
 * A workspace's `.skill/` changed from this page — the skill page's dialog,
 * the Skills panel's picker or folder import, the chat card, a fork. What
 * every skill page says about where it is installed (`…/installs`) and what
 * installing would do (`…/targets`) is then stale, whichever entry it is
 * about, so both are dropped by prefix (review round 1: only the dialog's own
 * install did this). A copy the agent installs in a turn is not seen here;
 * those answers catch up when the queries next go stale.
 */
export function invalidateHubInstalls(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ["skillHub", "installs"] });
  void qc.invalidateQueries({ queryKey: ["skillHub", "targets"] });
}
