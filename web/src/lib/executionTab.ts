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

/** The run an execution tab shows, or null for any other tab. */
export function runIdOf(path: string): string | null {
  return isExecutionPath(path) ? path.slice(PREFIX.length) : null;
}
