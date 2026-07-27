import { useMutation, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";
import { SettingRow } from "./SettingRow";

/**
 * "Knowledge graph" row on the collection settings panel (#534). When on, the
 * extraction pass reads every document in the collection and writes its metric
 * claims + mentions. A user-owned setting persisted through the standard
 * `PATCH /collection/{id}` (`use_graph`), like `auto_digest`.
 *
 * The switch deliberately does NOT start a run: extraction is expensive VLM/LLM
 * work, and a switch that silently spends it is the footgun #534 set out to
 * avoid — the same split the retrieval settings already use, where the toggle
 * persists and the rebuild button is its own press.
 *
 * The dispatch cronjob is weekly, though, so opting in and waiting would look
 * broken. Hence "extract now" inside the row, disabled until the collection has
 * opted in — the route answers `disabled` there, so offering it would promise
 * work that never runs.
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
    <SettingRow
      icon="branch"
      title={t("kb.useGraph.label")}
      desc={t("kb.useGraph.help")}
      on={collection.use_graph}
      disabled={toggle.isPending}
      onToggle={() => toggle.mutate(!collection.use_graph)}
      testId="kb-usegraph-toggle"
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
        <button
          type="button"
          className="kb-btn"
          data-testid="kb-usegraph-rebuild"
          disabled={!collection.use_graph || rebuild.isPending}
          onClick={() => rebuild.mutate()}
        >
          {t("kb.useGraph.rebuild")}
        </button>
        {rebuild.data && (
          <span
            data-testid="kb-usegraph-result"
            style={{ fontSize: pxToRem(11.5), color: "var(--text-paper-d)" }}
          >
            {rebuild.data.status === "disabled"
              ? t("kb.useGraph.rebuildDisabled")
              : t("kb.useGraph.rebuildQueued", { n: rebuild.data.queued })}
          </span>
        )}
      </div>
    </SettingRow>
  );
}
