/**
 * Per-model fitness verdict cards (#231 P6/P7) — the AI judge's "最後評分".
 * One card per model: a 0–100 fitness score + a markdown-ish summary of which
 * roles the model suits. "重新 AI 評分" re-judges the stored cells (no model
 * re-run) and refreshes the verdicts. Wire shapes: `api/sanity.ts`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { type SanityApi, type SanityVerdict, sanityApi } from "../api/sanity";
import { qk } from "../api/queryKeys";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

function scoreColor(score: number): string {
  if (score >= 80) return "var(--ok)";
  if (score >= 50) return "var(--warn)";
  return "var(--err)";
}

function VerdictCard({ v }: { v: SanityVerdict }) {
  return (
    <div
      data-testid={`verdict-${v.model}`}
      style={{
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        background: "var(--paper-2)",
        padding: "12px 14px",
        minWidth: 240,
        flex: "1 1 280px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <strong style={{ fontSize: pxToRem(13), overflowWrap: "anywhere" }}>{v.model}</strong>
        <span
          data-testid={`verdict-score-${v.model}`}
          style={{ fontSize: pxToRem(18), fontWeight: 700, color: scoreColor(v.score) }}
        >
          {v.score}
        </span>
      </div>
      <div
        style={{
          marginTop: 6,
          fontSize: pxToRem(12),
          color: "var(--text-paper-d)",
          whiteSpace: "pre-wrap",
          lineHeight: 1.5,
        }}
      >
        {v.summary}
      </div>
    </div>
  );
}

export function SanityVerdicts({ client = sanityApi }: { client?: SanityApi }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: meta } = useQuery({ queryKey: qk.sanity.meta, queryFn: () => client.getMeta() });
  const { data: verdicts } = useQuery({
    queryKey: qk.sanity.verdicts,
    queryFn: () => client.getVerdicts(),
  });
  const models = meta?.models ?? [];

  const rescore = useMutation({
    mutationFn: () => client.rescore(models),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: qk.sanity.verdicts });
      queryClient.invalidateQueries({ queryKey: ["sanity", "results"] });
    },
  });

  if (models.length === 0) return null;

  return (
    <div data-testid="sanity-verdicts" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <strong style={{ fontSize: pxToRem(13) }}>{t("sanity.verdicts.title")}</strong>
        <button
          type="button"
          data-testid="rescore"
          disabled={rescore.isPending}
          onClick={() => rescore.mutate()}
          style={{
            marginLeft: "auto",
            padding: "5px 11px",
            borderRadius: "var(--radius-btn)",
            border: "1px solid var(--paper-3)",
            background: "var(--paper-2)",
            fontSize: "var(--text-body-sm)",
            fontWeight: 600,
            cursor: rescore.isPending ? "default" : "pointer",
            opacity: rescore.isPending ? 0.6 : 1,
          }}
        >
          {rescore.isPending ? t("sanity.verdicts.scoring") : t("sanity.verdicts.rescore")}
        </button>
      </div>
      {verdicts && verdicts.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          {verdicts.map((v) => (
            <VerdictCard key={v.model} v={v} />
          ))}
        </div>
      ) : (
        <p data-testid="verdicts-empty" style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
          {t("sanity.verdicts.empty")}
        </p>
      )}
    </div>
  );
}
