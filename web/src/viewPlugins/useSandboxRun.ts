/**
 * `useSandboxRun(plugin, cmd, args)` — run one of a view plugin's sandbox
 * commands for the item the view is drawn in (#847/#848 PR1).
 *
 * The command runs in the item's own sandbox through the generic runner route
 * (`POST /a/{slug}/items/{id}/view-plugins/{plugin}/{cmd}`, body `{args}`),
 * which hands `args` to the plugin bundle's `launch <cmd> <args_json>` — so
 * `args` travels in argv, and a large input (a list of values, a table) must be
 * a workspace file path, not inline data: the route refuses an oversized one.
 *
 * The answer is the command's own output. A non-zero `exit_code` is part of it
 * (the command said no — read `stderr`); `error` is only for a call that never
 * ran: refused, unauthorized, outside an item workspace, network.
 *
 * Calls are cached per `(item, plugin, cmd, args)` like any other query; pass
 * `{enabled: false}` to hold one back until its inputs are known.
 */
import { useQuery } from "@tanstack/react-query";

import { useFileService } from "../api/fileService";
import { apiFetch, detailSentence, HttpError } from "../api/http";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";

/** What a sandbox command answered. */
export type SandboxRunResult = { stdout: string; stderr: string; exit_code: number };

/** The hook's return shape — part of the frozen SDK surface. */
export type SandboxRun = {
  data: SandboxRunResult | undefined;
  error: Error | null;
  isLoading: boolean;
  refetch: () => void;
};

export type SandboxRunArgs = Record<string, unknown>;

export function useSandboxRun(
  plugin: string,
  cmd: string,
  args: SandboxRunArgs,
  opts: { enabled?: boolean } = {},
): SandboxRun {
  const slug = useWorkspaceSlug();
  const itemId = useFileService().scopeId;
  const body = JSON.stringify({ args });
  const q = useQuery({
    queryKey: ["viewPluginRun", slug, itemId, plugin, cmd, body],
    enabled: opts.enabled ?? true,
    // A command's output is a pure function of its inputs AND the workspace
    // files it reads; refetch on focus would re-run a sandbox process for
    // nothing, so only an explicit `refetch()` or new args run it again.
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    queryFn: async (): Promise<SandboxRunResult> => {
      if (!slug || !itemId) {
        throw new Error(`view plugin "${plugin}" can only run "${cmd}" inside an item workspace`);
      }
      const resp = await apiFetch(
        `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/view-plugins/` +
          `${encodeURIComponent(plugin)}/${encodeURIComponent(cmd)}`,
        { method: "POST", headers: { "content-type": "application/json" }, body },
      );
      if (!resp.ok) {
        const why = (await detailSentence(resp.clone())) ?? `HTTP ${resp.status}`;
        throw new HttpError(resp.status, `view plugin "${plugin}" could not run "${cmd}": ${why}`);
      }
      return (await resp.json()) as SandboxRunResult;
    },
  });
  return {
    data: q.data,
    error: q.error,
    isLoading: q.isLoading,
    refetch: () => void q.refetch(),
  };
}
