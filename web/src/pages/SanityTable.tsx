/**
 * Diagnostics coverage table (#231) — replaces the old 2D matrix. ONE flat,
 * filterable table driven by the *full expected grid* (every model × question ×
 * the question's coverage levels) left-joined with results, so the never-run
 * blanks are visible (status ⬜未跑) and fillable in one click ("跑掉所有未跑的").
 * Columns: 題組 / 題目 / model / effort / 機械評分 / AI評分 / AI評語 / 回答 / 參考答案 / aux.
 * Wire shapes: `api/sanity.ts`.
 */

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type SanityApi,
  type SanityCell,
  type SanityMeta,
  type SanityQuestion,
  sanityApi,
} from "../api/sanity";
import { qk } from "../api/queryKeys";
import { ModalShell } from "../components/ModalShell";
import { type MsgKey, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

/** The levels a question is *expected* to be tested at — mirrors the backend's
 * `coverage_levels`: its auto_levels, or a single "none" cell when it declares
 * none. The expected grid is models × questions × these. */
export function coverageLevels(q: SanityQuestion): string[] {
  return q.auto_levels.length ? q.auto_levels : ["none"];
}

export type RowStatus = "missing" | "done" | "error";

export type CoverageRow = {
  model: string;
  question: SanityQuestion;
  level: string;
  cell: SanityCell | undefined;
  status: RowStatus;
};

function cellKey(questionKey: string, level: string): string {
  return `${questionKey}|${level}`;
}

/** The full expected grid left-joined with results → one row per expected cell. */
export function buildRows(
  models: string[],
  questions: SanityQuestion[],
  resultsByModel: Record<string, SanityCell[]>,
): CoverageRow[] {
  const rows: CoverageRow[] = [];
  for (const model of models) {
    const byCell = new Map<string, SanityCell>();
    for (const c of resultsByModel[model] ?? []) byCell.set(cellKey(c.question_key, c.level), c);
    for (const question of questions) {
      for (const level of coverageLevels(question)) {
        const cell = byCell.get(cellKey(question.key, level));
        const status: RowStatus = cell?.error ? "error" : cell ? "done" : "missing";
        rows.push({ model, question, level, cell, status });
      }
    }
  }
  return rows;
}

export function coverageStats(rows: CoverageRow[]): { done: number; total: number } {
  return { done: rows.filter((r) => r.status !== "missing").length, total: rows.length };
}

const STATUS_KEY: Record<RowStatus, MsgKey> = {
  missing: "sanity.table.status.missing",
  done: "sanity.table.status.done",
  error: "sanity.table.status.error",
};
const STATUS_COLOR: Record<RowStatus, string> = {
  missing: "var(--text-paper-d)",
  done: "var(--ok)",
  error: "var(--warn)",
};

function GradeTag({ grade }: { grade: string }) {
  const t = useT();
  if (grade === "pass") return <span style={{ color: "var(--ok)" }}>{t("sanity.table.grade.pass")}</span>;
  if (grade === "fail") return <span style={{ color: "var(--warn)" }}>{t("sanity.table.grade.fail")}</span>;
  return <span style={{ color: "var(--text-paper-d)" }}>—</span>;
}

function clip(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/** Read-only modal showing one cell's full answer + judge note + footer. */
function CellModal({
  row,
  levelLabel,
  onClose,
}: {
  row: CoverageRow;
  levelLabel: string;
  onClose: () => void;
}) {
  const t = useT();
  const cell = row.cell;
  const prompt = row.question.messages[row.question.messages.length - 1]?.content ?? "";
  const body = cell?.error || cell?.output || "";
  const footer = [
    cell?.grade ? t("sanity.table.modal.auto", { grade: cell.grade }) : null,
    cell?.ai_grade ? t("sanity.table.modal.ai", { grade: cell.ai_grade }) : null,
    cell?.reasoned ? t("sanity.table.modal.reasoned") : t("sanity.table.modal.noReasoning"),
    cell?.aux || null,
    cell ? `${cell.latency_ms}ms` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={`${levelLabel} · ${prompt}`}
      data-testid="sanity-cell-modal"
      width={720}
      maxWidth="90vw"
      panelStyle={{
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>
        {row.model} · {levelLabel} · {prompt}
      </strong>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          fontSize: pxToRem(13),
          lineHeight: 1.5,
          color: cell?.error ? "var(--warn)" : "var(--text-paper)",
        }}
      >
        {body}
      </div>
      {cell?.ai_note && (
        <div style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
          {t("sanity.table.modal.aiNote", { note: cell.ai_note })}
        </div>
      )}
      <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>{footer}</div>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          type="button"
          data-testid="sanity-cell-close"
          onClick={onClose}
          style={{
            height: 30,
            padding: "0 14px",
            borderRadius: "var(--radius-btn)",
            fontSize: pxToRem(13),
            cursor: "pointer",
            border: "1px solid var(--paper-3)",
            background: "var(--white)",
            color: "var(--text-paper)",
          }}
        >
          {t("sanity.table.close")}
        </button>
      </div>
    </ModalShell>
  );
}

const btn = (primary = false) => ({
  padding: "6px 12px",
  borderRadius: "var(--radius-btn)",
  border: "1px solid var(--paper-3)",
  background: primary ? "var(--accent-soft)" : "var(--paper-2)",
  color: primary ? "var(--accent-h)" : "var(--text-paper)",
  fontSize: "var(--text-body-sm)",
  fontWeight: 600,
  cursor: "pointer",
});

const th = {
  textAlign: "left" as const,
  padding: "6px 8px",
  color: "var(--text-paper-d)",
  whiteSpace: "nowrap" as const,
  position: "sticky" as const,
  top: 0,
  background: "var(--paper)",
};
const td = { padding: "7px 8px", verticalAlign: "top" as const, borderTop: "1px solid var(--paper-3)" };

export function SanityTable({ client = sanityApi }: { client?: SanityApi }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [selectedModels, setSelectedModels] = useState<Set<string> | null>(null);
  const [category, setCategory] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [pollUntil, setPollUntil] = useState(0);
  const [open, setOpen] = useState<CoverageRow | null>(null);

  const { data: meta } = useQuery<SanityMeta>({
    queryKey: qk.sanity.meta,
    queryFn: () => client.getMeta(),
  });
  const models = meta?.models ?? [];
  const questions = meta?.questions ?? [];
  const levelLabel = (lvl: string) =>
    meta?.levels.find((l) => l.level === lvl)?.label ?? lvl;

  const resultQueries = useQueries({
    queries: models.map((m) => ({
      queryKey: qk.sanity.results(m),
      queryFn: () => client.getResults(m),
      refetchInterval: () => (Date.now() < pollUntil ? 1500 : (false as const)),
    })),
  });
  const resultsByModel: Record<string, SanityCell[]> = {};
  models.forEach((m, i) => {
    resultsByModel[m] = resultQueries[i]?.data ?? [];
  });

  const afterWrite = () => {
    setPollUntil(Date.now() + 30_000);
    queryClient.invalidateQueries({ queryKey: ["sanity", "results"] });
  };
  const run = useMutation({
    mutationFn: (body: { model: string; question_key: string; level: string }) =>
      client.run({ scope: "cell", ...body }),
    onSettled: afterWrite,
  });
  const runMissing = useMutation({
    mutationFn: (vars: { models: string[]; category: string | null }) =>
      client.runMissing(vars.models, vars.category),
    onSettled: afterWrite,
  });

  if (models.length === 0) {
    return (
      <p data-testid="sanity-no-models" style={{ color: "var(--text-paper-d)", marginTop: 18 }}>
        {t("sanity.table.noModels")}
      </p>
    );
  }

  const shownModels = models.filter((m) => selectedModels === null || selectedModels.has(m));
  const categories = [...new Set(questions.map((q) => q.category))];
  const shownQuestions = category ? questions.filter((q) => q.category === category) : questions;

  const allRows = buildRows(shownModels, shownQuestions, resultsByModel);
  const rows = onlyMissing ? allRows.filter((r) => r.status === "missing") : allRows;
  const stats = coverageStats(allRows);

  const isOn = (m: string) => selectedModels === null || selectedModels.has(m);
  const toggleModel = (m: string) => {
    const next = new Set(selectedModels ?? models);
    if (next.has(m)) next.delete(m);
    else next.add(m);
    setSelectedModels(next);
  };

  return (
    <div data-testid="sanity-table" style={{ marginTop: 16 }}>
      {/* run / filter controls */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 10 }}>
        <span style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>
          {t("sanity.table.models")}
        </span>
        {models.map((m) => (
          <label
            key={m}
            style={{ display: "inline-flex", gap: 4, alignItems: "center", fontSize: "var(--text-body-sm)" }}
          >
            <input
              type="checkbox"
              data-testid={`model-toggle-${m}`}
              checked={isOn(m)}
              onChange={() => toggleModel(m)}
            />
            {m}
          </label>
        ))}
        <select
          data-testid="category-filter"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          style={{ padding: "4px 8px", borderRadius: 6, border: "1px solid var(--paper-3)" }}
        >
          <option value="">{t("sanity.table.allCategories")}</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <label style={{ display: "inline-flex", gap: 4, alignItems: "center", fontSize: "var(--text-body-sm)" }}>
          <input
            type="checkbox"
            data-testid="only-missing"
            checked={onlyMissing}
            onChange={(e) => setOnlyMissing(e.target.checked)}
          />
          {t("sanity.table.onlyMissing")}
        </label>
        <button
          type="button"
          data-testid="run-missing"
          onClick={() =>
            runMissing.mutate({ models: shownModels, category: category || null })
          }
          style={{ ...btn(true), marginLeft: "auto" }}
        >
          {t("sanity.table.runMissing")}
        </button>
      </div>

      <div data-testid="coverage-summary" style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", marginBottom: 8 }}>
        {t("sanity.table.summary", { done: stats.done, total: stats.total })}
        {stats.total - stats.done > 0
          ? t("sanity.table.summaryRemaining", { n: stats.total - stats.done })
          : t("sanity.table.summaryAllRun")}
      </div>

      <div style={{ overflow: "auto", maxHeight: "62vh" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--text-body-sm)" }}>
          <thead>
            <tr>
              {(
                [
                  "sanity.table.col.category",
                  "sanity.table.col.question",
                  "sanity.table.col.model",
                  "sanity.table.col.effort",
                  "sanity.table.col.status",
                  "sanity.table.col.grade",
                  "sanity.table.col.ai",
                  "sanity.table.col.aiNote",
                  "sanity.table.col.answer",
                  "sanity.table.col.expected",
                  "sanity.table.col.aux",
                ] as const
              ).map((key) => (
                <th key={key} style={th}>
                  {t(key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.model}|${r.question.key}|${r.level}`} data-testid={`row-${r.model}-${r.question.key}-${r.level}`}>
                <td style={{ ...td, color: "var(--text-paper-d)" }}>{r.question.category}</td>
                <td style={{ ...td, maxWidth: 220 }}>
                  {clip(r.question.messages[r.question.messages.length - 1]?.content ?? "", 60)}
                </td>
                <td style={{ ...td, whiteSpace: "nowrap" }}>{r.model}</td>
                <td style={td}>{levelLabel(r.level)}</td>
                <td style={{ ...td, color: STATUS_COLOR[r.status], fontWeight: 600, whiteSpace: "nowrap" }}>
                  <span data-testid={`status-${r.model}-${r.question.key}-${r.level}`}>
                    {t(STATUS_KEY[r.status])}
                  </span>
                </td>
                <td style={td}>{r.cell ? <GradeTag grade={r.cell.grade} /> : "—"}</td>
                <td style={td}>{r.cell ? <GradeTag grade={r.cell.ai_grade} /> : "—"}</td>
                <td style={{ ...td, maxWidth: 160, color: "var(--text-paper-d)" }}>
                  {clip(r.cell?.ai_note ?? "", 50)}
                </td>
                <td style={{ ...td, maxWidth: 220 }}>
                  {r.cell ? (
                    <button
                      type="button"
                      data-testid={`open-${r.model}-${r.question.key}-${r.level}`}
                      onClick={() => setOpen(r)}
                      style={{
                        border: "none",
                        background: "none",
                        padding: 0,
                        font: "inherit",
                        textAlign: "left",
                        cursor: "pointer",
                        color: r.cell.error ? "var(--warn)" : "inherit",
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {clip(r.cell.error || r.cell.output, 80)}
                    </button>
                  ) : (
                    <button
                      type="button"
                      data-testid={`run-${r.model}-${r.question.key}-${r.level}`}
                      onClick={() =>
                        run.mutate({ model: r.model, question_key: r.question.key, level: r.level })
                      }
                      style={btn()}
                    >
                      {t("sanity.table.run")}
                    </button>
                  )}
                </td>
                <td style={{ ...td, maxWidth: 180, color: "var(--text-paper-d)" }}>
                  {clip(r.question.expected, 50)}
                </td>
                <td style={{ ...td, color: "var(--text-paper-d)", whiteSpace: "nowrap" }}>
                  {r.cell?.aux || ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {open && <CellModal row={open} levelLabel={levelLabel(open.level)} onClose={() => setOpen(null)} />}
    </div>
  );
}
