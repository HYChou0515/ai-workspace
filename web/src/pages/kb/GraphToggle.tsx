import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

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

  // #697: the corpus's own criterion for what deserves to be a thing. The
  // prompt's own description ("anything a reader would consider a distinct
  // thing") is not a criterion — every noun passes it — and which things matter
  // is domain knowledge, so it is typed here by the people who own the corpus
  // rather than guessed by whoever wrote the prompt.
  const stored = collection.graph_guidance ?? "";
  const [criterion, setCriterion] = useState(stored);
  useEffect(() => setCriterion(stored), [stored]);
  const saveCriterion = useMutation({
    mutationFn: () =>
      client.updateCollection(collection.resource_id, { graph_guidance: criterion }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
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
      <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        <label
          htmlFor="kb-graph-guidance"
          style={{ fontSize: pxToRem(12), fontWeight: 600, color: "var(--text-paper)" }}
        >
          {t("kb.graphGuidance.label")}
        </label>
        <p
          style={{
            margin: 0,
            fontSize: pxToRem(11.5),
            lineHeight: 1.45,
            color: "var(--text-paper-d)",
          }}
        >
          {t("kb.graphGuidance.help")}
        </p>
        <textarea
          id="kb-graph-guidance"
          data-testid="kb-graph-guidance"
          value={criterion}
          onChange={(e) => setCriterion(e.target.value)}
          placeholder={t("kb.graphGuidance.placeholder")}
          rows={4}
          style={{
            width: "100%",
            resize: "vertical",
            padding: "8px 10px",
            borderRadius: 8,
            border: "1px solid var(--paper-3)",
            background: "var(--paper)",
            font: "inherit",
            fontSize: pxToRem(13),
            lineHeight: 1.5,
            boxSizing: "border-box",
          }}
        />
        <div>
          <button
            type="button"
            className="kb-btn"
            data-testid="kb-graph-guidance-save"
            // Dirty-gated: specstar writes a revision per update, so an
            // accidental press should not add a version that changed nothing.
            disabled={criterion === stored || saveCriterion.isPending}
            onClick={() => saveCriterion.mutate()}
          >
            {t("kb.graphGuidance.save")}
          </button>
        </div>
      </div>
    </SettingRow>
  );
}
