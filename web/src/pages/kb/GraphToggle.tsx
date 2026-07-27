import { useMutation, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

/**
 * "Knowledge graph" toggle on the collection settings panel (#534). When on, the
 * extraction pass reads every document in the collection and writes its metric
 * claims + mentions. It's a user-owned setting persisted through the standard
 * `PATCH /collection/{id}` (`use_graph`), like `auto_digest`.
 *
 * The toggle deliberately does NOT start a run: extraction is expensive VLM/LLM
 * work, and a switch that silently spends it is the footgun #534 set out to
 * avoid. The dispatch cronjob is weekly, though, so waiting for it would make a
 * freshly opted-in collection look broken — hence the explicit "extract now"
 * button beside it, disabled until the collection has opted in (the route
 * answers `disabled` there, so offering it would promise work that never runs).
 */
export function GraphToggle({
  collection,
  client = kbApi,
}: {
  collection: KbCollection;
  client?: KbApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (next: boolean) =>
      client.updateCollection(collection.resource_id, { use_graph: next }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });
  const rebuild = useMutation({
    mutationFn: () => client.rebuildGraph(collection.resource_id),
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label
        className="kb-usegraph-toggle"
        style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}
      >
        <input
          type="checkbox"
          data-testid="kb-usegraph-toggle"
          checked={collection.use_graph}
          disabled={toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
        />
        <span style={{ display: "inline-flex", flexDirection: "column", lineHeight: 1.3 }}>
          <span style={{ fontSize: pxToRem(13), fontWeight: 600, color: "var(--text-paper)" }}>
            {t("kb.useGraph.label")}
          </span>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
            {t("kb.useGraph.help")}
          </span>
        </span>
      </label>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <button
          type="button"
          data-testid="kb-usegraph-rebuild"
          disabled={!collection.use_graph || rebuild.isPending}
          onClick={() => rebuild.mutate()}
          style={{ fontSize: pxToRem(12) }}
        >
          {t("kb.useGraph.rebuild")}
        </button>
        {rebuild.data && (
          <span
            data-testid="kb-usegraph-result"
            style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}
          >
            {rebuild.data.status === "disabled"
              ? t("kb.useGraph.rebuildDisabled")
              : t("kb.useGraph.rebuildQueued", { n: rebuild.data.queued })}
          </span>
        )}
      </div>
    </div>
  );
}
