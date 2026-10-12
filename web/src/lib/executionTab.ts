import type { RunStatus } from "../api/workflows";

/**
 * An execution's editor tab (plan-workflow-out-of-chat D7). A tab is known by
 * its path, so an execution gets one no workspace file can have: workspace paths
 * are absolute (`/notes.md`) or relative (`notes.md`) and never carry a scheme.
 * The shell asks `isExecutionPath` before treating a tab as a file — a file
 * that is absent closes its tab, an execution never is one.
 */

const PREFIX = "workflow-run:";

export function executionPath(runId: string): string {
  return `${PREFIX}${runId}`;
}

export function isExecutionPath(path: string): boolean {
  return path.startsWith(PREFIX);
}

/** The item page's address parameter that opens an execution's tab: the
 * schedules overview links a schedule's last run this way (`?open=<path>`). */
export const OPEN_PARAM = "open";

/** The execution tab an address asks the item page to open, or null — only an
 * execution's path; the parameter is not a way to open arbitrary files. */
export function executionFromAddress(search: string): string | null {
  const path = new URLSearchParams(search).get(OPEN_PARAM);
  return path && isExecutionPath(path) && runIdOf(path) ? path : null;
}

/** The run an execution tab shows, or null for any other tab. */
export function runIdOf(path: string): string | null {
  return isExecutionPath(path) ? path.slice(PREFIX.length) : null;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** What an execution is called (plan D10): its workflow and when it started,
 * "Classify wafers · 10/12 14:03", in local time. Built by hand rather than by
 * `Intl`, whose output follows the machine's locale. */
export function executionName(title: string, startedMs: number | null | undefined): string {
  if (startedMs == null) return title;
  const d = new Date(startedMs);
  return `${title} · ${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STATUS_TEXT: Record<RunStatus, string> = {
  pending: "Starting",
  running: "Running",
  awaiting_human: "Waiting for you",
  done: "Done",
  error: "Failed",
  cancelled: "Stopped",
};

/** An execution's status in the words a person uses. */
export function statusText(status: RunStatus): string {
  return STATUS_TEXT[status];
}
