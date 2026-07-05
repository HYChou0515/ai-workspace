/**
 * Header indicator for AI-feature health (#51 P5): one dot that
 * summarises the latest probe results and links to /diagnostics.
 *
 * Q6 (plan-sanity-checks): a failing check WARNS, it never gates —
 * this dot is informational only. Copy is jargon-free per the UI-copy
 * rule: states speak of "AI features", never of check ids or models.
 * Polls gently (and faster while a round is running) so a fixed model
 * shows up without a reload.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { type HealthApi, healthApi } from "../api/health";
import { qk } from "../api/queryKeys";

type DotState = "ok" | "warn" | "unknown" | "running";

const COPY: Record<DotState, string> = {
  ok: "AI features are working normally",
  warn: "Some AI features may not be working — open diagnostics for details",
  unknown: "AI feature status hasn't been checked yet",
  running: "Checking AI features…",
};

const DOT_COLOR: Record<DotState, string> = {
  ok: "var(--ok)",
  warn: "var(--warn)",
  unknown: "var(--text-paper-d)",
  running: "var(--info)",
};

export function HealthDot({ client = healthApi }: { client?: HealthApi }) {
  const { data } = useQuery({
    queryKey: qk.health,
    queryFn: () => client.getChecks(),
    refetchInterval: (query) => (query.state.data?.running ? 1500 : 60_000),
  });

  if (!data || data.checks.length === 0) return null;

  let state: DotState;
  if (data.running) {
    state = "running";
  } else if (data.checks.some((c) => c.status === "fail" || c.status === "error")) {
    state = "warn";
  } else if (data.checks.some((c) => c.status === "pass")) {
    // skips don't count against health; at least one positive signal → ok
    state = "ok";
  } else {
    state = "unknown";
  }

  return (
    <Link
      to="/diagnostics"
      className="health-dot"
      aria-label={COPY[state]}
      title={COPY[state]}
      data-health={state}
    >
      <span
        aria-hidden
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: DOT_COLOR[state],
          animation: state === "running" ? "health-dot-pulse 1.2s ease-in-out infinite" : undefined,
        }}
      />
    </Link>
  );
}
