/**
 * Model-sanity matrix (Diagnostics): for one selected model, a grid of
 * behavioural questions (rows) × reasoning levels (columns). Each cell shows the
 * model's answer with a mechanical green/red dot where the question can be
 * auto-graded, an aux hint (e.g. character count) otherwise, and whether the
 * model emitted reasoning. Empty cells offer ▶ run; filled cells offer ↻ rerun.
 * "Run battery" pre-fills the auto-run cells. Wire shapes: `api/sanity.ts`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  type SanityApi,
  type SanityCell,
  type SanityLevel,
  type SanityQuestion,
  type SanityRunBody,
  sanityApi,
} from "../api/sanity";
import { qk } from "../api/queryKeys";
import { pxToRem } from "../lib/pxToRem";

function cellKey(questionKey: string, level: string): string {
  return `${questionKey}|${level}`;
}

/** What a cell preview opens: the full result plus enough context to read it. */
type OpenCell = { cell: SanityCell; level: SanityLevel; question: SanityQuestion };

function Dot({ grade, error }: { grade: string; error: string }) {
  if (error) return <span title={error} style={{ color: "var(--warn)" }}>●</span>;
  if (grade === "pass") return <span title="passed" style={{ color: "var(--ok, #2e9e5b)" }}>●</span>;
  if (grade === "fail") return <span title="failed" style={{ color: "var(--warn)" }}>●</span>;
  return null; // eyeball-only question → no dot
}

/** The in-cell preview: the grade dot + a truncated, clickable answer. The text
 * is a real <button> (not a clickable <span>) so it stays keyboard-reachable,
 * matching the existing ↻ rerun link-button. Clicking opens the full answer. */
function CellPreview({
  cell,
  testId,
  onOpen,
}: {
  cell: SanityCell;
  testId: string;
  onOpen: () => void;
}) {
  const [hover, setHover] = useState(false);
  const preview = cell.error
    ? cell.error
    : cell.output.slice(0, 120) + (cell.output.length > 120 ? "…" : "");
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
      <Dot grade={cell.grade} error={cell.error} />
      <button
        type="button"
        data-testid={testId}
        onClick={onOpen}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        title="點擊看完整內容"
        style={{
          border: "none",
          background: "none",
          padding: 0,
          margin: 0,
          font: "inherit",
          textAlign: "left",
          cursor: "pointer",
          color: hover ? "var(--accent-h)" : "inherit",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
        }}
      >
        {preview}
      </button>
    </div>
  );
}

/** Read-only modal showing one cell's full answer (or error). Dedicated rather
 * than the shared confirm Dialog because model answers run long (e.g. a 300-字
 * essay) and need a wider, scrollable surface, not a 420px confirm body. */
function OutputModal({ open, onClose }: { open: OpenCell; onClose: () => void }) {
  const { cell, level, question } = open;
  const prompt = question.messages[question.messages.length - 1]?.content ?? "";
  const body = cell.error || cell.output;
  const footer = [
    cell.grade || null,
    cell.reasoned ? "reasoned" : "no reasoning",
    cell.aux || null,
    `${cell.latency_ms}ms`,
  ]
    .filter(Boolean)
    .join(" · ");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${level.label} · ${prompt}`}
        data-testid="sanity-output-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 720,
          maxWidth: "90vw",
          maxHeight: "80vh",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
          padding: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <strong style={{ fontSize: pxToRem(14) }}>
          {level.label} · {prompt}
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
            color: cell.error ? "var(--warn)" : "var(--text-paper)",
          }}
        >
          {body}
        </div>
        <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>{footer}</div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            data-testid="sanity-output-close"
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
            關閉
          </button>
        </div>
      </div>
    </div>
  );
}

export function SanityMatrix({ client = sanityApi }: { client?: SanityApi }) {
  const queryClient = useQueryClient();
  const [model, setModel] = useState<string>("");
  const [pollUntil, setPollUntil] = useState(0);
  const [openCell, setOpenCell] = useState<OpenCell | null>(null);

  const { data: meta } = useQuery({ queryKey: qk.sanity.meta, queryFn: () => client.getMeta() });
  const models = meta?.models ?? [];
  const levels = meta?.levels ?? [];
  const questions = meta?.questions ?? [];
  const selected = model || models[0] || "";

  const { data: cells } = useQuery({
    queryKey: qk.sanity.results(selected),
    queryFn: () => client.getResults(selected),
    enabled: !!selected,
    // Poll for ~30s after a run so cells appear as the background jobs finish.
    refetchInterval: () => (Date.now() < pollUntil ? 1500 : false),
  });

  const run = useMutation({
    mutationFn: (body: SanityRunBody) => client.run(body),
    onSettled: () => {
      setPollUntil(Date.now() + 30_000);
      queryClient.invalidateQueries({ queryKey: qk.sanity.results(selected) });
    },
  });

  const byCell = new Map<string, SanityCell>();
  for (const c of cells ?? []) byCell.set(cellKey(c.question_key, c.level), c);

  if (models.length === 0) {
    return (
      <p data-testid="sanity-no-models" style={{ color: "var(--text-paper-d)", marginTop: 18 }}>
        No chat models are configured, so there's nothing to check here.
      </p>
    );
  }

  return (
    <div data-testid="sanity-matrix" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12 }}>
        <label style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>
          Model
        </label>
        <select
          data-testid="sanity-model"
          value={selected}
          onChange={(e) => setModel(e.target.value)}
          style={{
            padding: "5px 8px",
            borderRadius: 6,
            border: "1px solid var(--paper-3)",
            background: "var(--paper-2)",
            fontSize: "var(--text-body-sm)",
          }}
        >
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <button
          type="button"
          data-testid="sanity-run-battery"
          onClick={() => run.mutate({ model: selected, scope: "battery" })}
          style={{
            marginLeft: "auto",
            padding: "6px 12px",
            borderRadius: 7,
            border: "1px solid var(--paper-3)",
            background: "var(--paper-2)",
            fontSize: "var(--text-body-sm)",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Run battery
        </button>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--text-body-sm)" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-paper-d)" }}>
                Question
              </th>
              {levels.map((lvl) => (
                <th
                  key={lvl.level}
                  style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-paper-d)" }}
                >
                  {lvl.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {questions.map((q) => (
              <tr key={q.key} style={{ borderTop: "1px solid var(--paper-3)" }}>
                <td style={{ padding: "8px", verticalAlign: "top", maxWidth: 260 }}>
                  <div style={{ fontWeight: 500 }}>{q.messages[q.messages.length - 1]?.content}</div>
                  <div style={{ color: "var(--text-paper-d)", marginTop: 2 }}>↳ {q.expected}</div>
                </td>
                {levels.map((lvl) => {
                  const cell = byCell.get(cellKey(q.key, lvl.level));
                  return (
                    <td
                      key={lvl.level}
                      data-testid={`cell-${q.key}-${lvl.level}`}
                      style={{ padding: "8px", verticalAlign: "top", minWidth: 150 }}
                    >
                      {cell ? (
                        <div>
                          <CellPreview
                            cell={cell}
                            testId={`output-${q.key}-${lvl.level}`}
                            onOpen={() => setOpenCell({ cell, level: lvl, question: q })}
                          />
                          <div style={{ marginTop: 3, color: "var(--text-paper-d)", fontSize: pxToRem(11) }}>
                            {cell.reasoned ? "reasoned" : "no reasoning"}
                            {cell.aux ? ` · ${cell.aux}` : ""}
                            {" · "}
                            <button
                              type="button"
                              data-testid={`rerun-${q.key}-${lvl.level}`}
                              onClick={() =>
                                run.mutate({
                                  model: selected,
                                  scope: "cell",
                                  question_key: q.key,
                                  level: lvl.level,
                                })
                              }
                              style={{
                                border: "none",
                                background: "none",
                                color: "var(--accent-h)",
                                cursor: "pointer",
                                padding: 0,
                                fontSize: pxToRem(11),
                              }}
                            >
                              ↻ rerun
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          data-testid={`run-${q.key}-${lvl.level}`}
                          onClick={() =>
                            run.mutate({
                              model: selected,
                              scope: "cell",
                              question_key: q.key,
                              level: lvl.level,
                            })
                          }
                          style={{
                            border: "1px solid var(--paper-3)",
                            background: "transparent",
                            borderRadius: 6,
                            color: "var(--text-paper-d)",
                            cursor: "pointer",
                            padding: "2px 8px",
                            fontSize: pxToRem(12),
                          }}
                        >
                          ▶ run
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {openCell && <OutputModal open={openCell} onClose={() => setOpenCell(null)} />}
    </div>
  );
}
