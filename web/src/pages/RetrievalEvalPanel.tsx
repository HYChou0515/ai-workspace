/**
 * #535 — the retrieval-eval face: fire a pass, read the quality of retrieval.
 *
 * One card per (knowledge base × run). The two questions a reader actually has
 * — "does retrieval find the source passage?" and "does it at least find the
 * right document?" — each get a big MRR numeral plus recall@k meters, in plain
 * words. Jargon stays out of the surface; the legend explains the two numbers
 * once. POST /api/eval-job (the auto route IS the enqueue) fires a pass;
 * in-flight runs show as live progress bars.
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

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** One metric family — a big MRR numeral beside four recall@k meters. */
function MetricGroup({
  title,
  mrr,
  recall,
}: {
  title: string;
  mrr: number;
  recall: Record<string, number>;
}) {
  const t = useT();
  return (
    <div className="eval-card__group">
      <div className="eval-card__group-title">{title}</div>
      <div className="eval-card__group-body">
        <div className="eval-card__mrr">
          <span className="eval-card__mrr-num">{mrr.toFixed(2)}</span>
          <span className="eval-card__mrr-label">{t("eval.mrr")}</span>
        </div>
        <div className="eval-card__bars">
          {KS.map((k) => {
            const v = recall[k] ?? 0;
            return (
              <div className="eval-card__bar" key={k} title={t("eval.recallTitle", { k })}>
                <span className="eval-card__bar-k">{t("eval.topK", { k })}</span>
                <span className="eval-card__bar-track">
                  <span className="eval-card__bar-fill" style={{ width: pct(v) }} />
                </span>
                <span className="eval-card__bar-v">{pct(v)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

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
  const shown = (results.data ?? []).filter((r) => r.n_kept > 0);

  return (
    <div className="eval-panel" data-testid="eval-panel">
      <div className="eval-panel__bar">
        <div>
          <p className="eval-panel__blurb">{t("eval.blurb")}</p>
          {/* The two numbers, explained once — never again per row. */}
          <p className="eval-panel__legend">{t("eval.legend")}</p>
        </div>
        <button
          type="button"
          className="eval-panel__fire"
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
          {running.map((r) => {
            const doneN = r.done.length;
            const frac = r.total > 0 ? doneN / r.total : 0;
            return (
              <li key={`${r.collection_id}:${r.run_label}`} className="eval-panel__run">
                <span className="eval-panel__run-name">
                  {names.get(r.collection_id) ?? r.collection_id}
                </span>
                <span className="eval-panel__run-label">{r.run_label}</span>
                <span className="eval-card__bar-track eval-panel__run-track">
                  <span className="eval-card__bar-fill" style={{ width: pct(frac) }} />
                </span>
                <span className="eval-panel__run-count">
                  {doneN}/{r.total}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {results.isPending ? (
        <Skeleton style={{ height: 180 }} />
      ) : shown.length === 0 ? (
        <p className="rvw__empty" data-testid="eval-empty">
          {t("eval.empty")}
        </p>
      ) : (
        <div className="eval-panel__grid" data-testid="eval-results">
          {shown.map((r) => (
            <article className="eval-card" key={`${r.collection_id}:${r.run_label}`}>
              <header className="eval-card__head">
                <h3 className="eval-card__kb">{names.get(r.collection_id) ?? r.collection_id}</h3>
                <span className="eval-card__run">{r.run_label}</span>
              </header>
              <p className="eval-card__sample">
                {t("eval.sample", { kept: String(r.n_kept), gen: String(r.n_generated) })}
              </p>
              <MetricGroup title={t("eval.chunkTitle")} mrr={r.mrr_chunk} recall={r.recall_chunk} />
              <MetricGroup title={t("eval.docTitle")} mrr={r.mrr_doc} recall={r.recall_doc} />
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
