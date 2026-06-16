/**
 * Model-sanity matrix (Diagnostics): for one selected model, a grid of
 * behavioural questions (rows) × reasoning levels (columns). Each cell shows the
 * model's answer with a mechanical green/red dot where the question can be
 * auto-graded, an aux hint (e.g. character count) otherwise, and whether the
 * model emitted reasoning. Empty cells offer ▶ run; filled cells offer ↻ rerun.
 * "Run battery" pre-fills the auto-run cells. Wire shapes: `api/sanity.ts`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { type SanityApi, type SanityCell, type SanityRunBody, sanityApi } from "../api/sanity";
import { qk } from "../api/queryKeys";

function cellKey(questionKey: string, level: string): string {
  return `${questionKey}|${level}`;
}

function Dot({ grade, error }: { grade: string; error: string }) {
  if (error) return <span title={error} style={{ color: "var(--warn)" }}>●</span>;
  if (grade === "pass") return <span title="passed" style={{ color: "var(--ok, #2e9e5b)" }}>●</span>;
  if (grade === "fail") return <span title="failed" style={{ color: "var(--warn)" }}>●</span>;
  return null; // eyeball-only question → no dot
}

export function SanityMatrix({ client = sanityApi }: { client?: SanityApi }) {
  const queryClient = useQueryClient();
  const [model, setModel] = useState<string>("");
  const [pollUntil, setPollUntil] = useState(0);

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
                          <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                            <Dot grade={cell.grade} error={cell.error} />
                            <span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                              {cell.error
                                ? cell.error
                                : cell.output.slice(0, 120) + (cell.output.length > 120 ? "…" : "")}
                            </span>
                          </div>
                          <div style={{ marginTop: 3, color: "var(--text-paper-d)", fontSize: 11 }}>
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
                                fontSize: 11,
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
                            fontSize: 12,
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
    </div>
  );
}
