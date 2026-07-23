/**
 * #535 — the retrieval-eval face the engine never had.
 *
 * The fan-out pipeline (synthetic questions → recall@k / MRR per collection)
 * always produced `EvalResult` rows; until #622 nothing could fire it, and no
 * page showed the metrics. This panel does both, on specstar's own surface:
 * POST /api/eval-job (creating the dispatch row IS the enqueue), read back
 * /api/eval-run (fan-out progress) + /api/eval-result (the metrics).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "../api/http";
import { kbApi } from "../api/kb";
import { qk } from "../api/queryKeys";
import { Skeleton } from "../components/Skeleton";
import { useT } from "../lib/i18n";

type EvalRun = {
  collection_id: string;
  run_label: string;
  status: string;
  total: number;
  done: number[];
  failed: number[];
};

type EvalResult = {
  collection_id: string;
  run_label: string;
  n_generated: number;
  n_kept: number;
  recall_chunk: Record<string, number>;
  mrr_chunk: number;
  recall_doc: Record<string, number>;
  mrr_doc: number;
};

async function rows<T>(path: string): Promise<T[]> {
  const resp = await apiFetch(path);
  if (!resp.ok) throw new Error(`${path} ${resp.status}`);
  return resp.json();
}

const KS = ["1", "3", "5", "10"];

export function RetrievalEvalPanel() {
  const t = useT();
  const qc = useQueryClient();
  const runs = useQuery({
    queryKey: qk.kb.evalRuns(),
    queryFn: () => rows<EvalRun>("/eval-run/data"),
    // Live while a pass is moving; settle down once everything is done.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => r.status === "running") ? 2000 : false,
  });
  const results = useQuery({
    queryKey: qk.kb.evalResults(),
    queryFn: () => rows<EvalResult>("/eval-result/data"),
  });
  const collections = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => kbApi.listCollections(),
  });
  const names = new Map((collections.data ?? []).map((c) => [c.resource_id, c.name]));

  const fire = useMutation({
    mutationFn: async () => {
      const label = `run-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}`;
      const resp = await apiFetch("/eval-job", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: { kind: "dispatch", run_label: label } }),
      });
      if (!resp.ok) throw new Error(`eval-job ${resp.status}`);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.evalRuns() });
      void qc.invalidateQueries({ queryKey: qk.kb.evalResults() });
    },
  });

  const running = (runs.data ?? []).filter((r) => r.status === "running");

  return (
    <div className="eval-panel" data-testid="eval-panel">
      <div className="eval-panel__bar">
        <p className="eval-panel__blurb">{t("eval.blurb")}</p>
        <button
          type="button"
          className="kb-btn"
          disabled={fire.isPending}
          onClick={() => fire.mutate()}
        >
          {t("eval.run")}
        </button>
      </div>
      {fire.isError ? (
        <p className="rvw__empty" role="alert">
          {t("eval.fireFailed")}
        </p>
      ) : null}

      {running.length > 0 && (
        <ul className="eval-panel__running" data-testid="eval-running">
          {running.map((r) => (
            <li key={`${r.collection_id}:${r.run_label}`}>
              {names.get(r.collection_id) ?? r.collection_id} · {r.run_label} —{" "}
              {r.done.length}/{r.total}
            </li>
          ))}
        </ul>
      )}

      {results.isPending ? (
        <Skeleton style={{ height: 180 }} />
      ) : (results.data ?? []).length === 0 ? (
        <p className="rvw__empty" data-testid="eval-empty">
          {t("eval.empty")}
        </p>
      ) : (
        <table className="eval-panel__table" data-testid="eval-table">
          <thead>
            <tr>
              <th>{t("eval.col.collection")}</th>
              <th>{t("eval.col.run")}</th>
              <th>{t("eval.col.kept")}</th>
              {KS.map((k) => (
                <th key={k}>R@{k}</th>
              ))}
              <th>MRR</th>
              <th>{t("eval.col.docMrr")}</th>
            </tr>
          </thead>
          <tbody>
            {(results.data ?? [])
              .filter((r) => r.n_kept > 0) // an empty collection's zero-row says nothing
              .map((r) => (
                <tr key={`${r.collection_id}:${r.run_label}`}>
                  <td>{names.get(r.collection_id) ?? r.collection_id}</td>
                  <td>{r.run_label}</td>
                  <td>{r.n_kept}</td>
                  {KS.map((k) => (
                    <td key={k}>{(r.recall_chunk[k] ?? 0).toFixed(2)}</td>
                  ))}
                  <td>{r.mrr_chunk.toFixed(2)}</td>
                  <td>{r.mrr_doc.toFixed(2)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
