/**
 * `view: chart` — the panel.
 *
 * The document is checked against the shared schema here first (a refused spec
 * shows its errors and runs nothing), then sent WHOLE to the sandbox's `query`
 * as JSON — JSON is YAML 1.2, so the sandbox reads it with the same parser it
 * reads view files with. The answer becomes an ECharts option (`toOption`),
 * `highlight:` already in it (unlit rows dimmed in their data); a brush, lasso or
 * legend click becomes a `Selection` in layer rows, local to the view until
 * markings (PR 3) link views.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { type EntityViewProps, useSandboxRun, viewDocument } from "@aiws/view-sdk";

import { createChart, type Chart } from "./echarts";
import { type Answer, type Built, toOption } from "./option";
import type { Cells, RasterImage } from "./raster";
import { type BrushSelected, type Selection, selectionFromBrush, selectionFromLegend } from "./selection";
import { specErrors } from "./spec";

export const PLUGIN = "chart";
const FORMAT = 1;
/** A lattice is drawn at least this many pixels across before ECharts scales
 * it, nearest-neighbour, so its cells stay sharp-edged instead of smeared. */
const RASTER_MIN_PX = 512;

function Notice({ role = "status", children }: { role?: "status" | "alert"; children: ReactNode }) {
  const color = role === "alert" ? "var(--err)" : "var(--text-paper-d)";
  return (
    <div role={role} style={{ padding: 12, color, whiteSpace: "pre-wrap" }}>
      {children}
    </div>
  );
}

/** The grid's pixels as a canvas ECharts can draw, upscaled without smoothing. */
function gridCanvas({ image }: { cells: Cells; image: RasterImage }): HTMLCanvasElement {
  const scale = Math.max(1, Math.ceil(RASTER_MIN_PX / Math.max(image.width, image.height, 1)));
  const small = document.createElement("canvas");
  small.width = image.width;
  small.height = image.height;
  const pixels = new Uint8ClampedArray(image.data); // an ArrayBuffer-backed copy, as ImageData requires
  small.getContext("2d")?.putImageData(new ImageData(pixels, image.width, image.height), 0, 0);
  const big = document.createElement("canvas");
  big.width = image.width * scale;
  big.height = image.height * scale;
  const ctx = big.getContext("2d");
  if (ctx) {
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(small, 0, 0, big.width, big.height);
  }
  return big;
}

function readAnswer(stdout: string): Answer | string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return "the chart's sandbox answered something that is not JSON";
  }
  const format = (parsed as { format?: unknown }).format;
  if (format !== FORMAT) return `the chart's sandbox answered in format ${String(format)}; this renderer reads ${FORMAT}`;
  return parsed as Answer;
}

function Plot({ doc, answer }: { doc: Record<string, unknown>; answer: Answer }) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [selection, setSelection] = useState<Selection[]>([]);
  const built: Built = useMemo(() => toOption(doc, answer, { gridImage: gridCanvas }), [doc, answer]);
  const builtRef = useRef(built);
  builtRef.current = built;

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current);
    chartRef.current = chart;
    chart.on("brushselected", (p) => setSelection(selectionFromBrush(p as BrushSelected, builtRef.current)));
    chart.on("legendselectchanged", (p) =>
      setSelection(selectionFromLegend((p as { selected: Record<string, boolean> }).selected, builtRef.current)),
    );
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => chart.resize());
    resize?.observe(el.current);
    return () => {
      resize?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(built.option, true);
    setSelection([]);
  }, [built]);

  const count = selection.reduce((n, s) => n + s.rows.length, 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 360 }}>
      {(built.notes.length > 0 || count > 0) && (
        <div style={{ display: "flex", gap: 12, padding: "4px 12px", fontSize: 12, color: "var(--text-paper-d)" }}>
          {built.notes.map((n) => (
            <span key={n}>{n}</span>
          ))}
          {count > 0 && <span>{count} selected</span>}
        </div>
      )}
      <div ref={el} style={{ flex: 1, minHeight: 320 }} />
    </div>
  );
}

export function ChartView({ spec }: EntityViewProps) {
  // Keyed on the text: the container rebuilds `spec` on every render.
  const text = JSON.stringify(viewDocument(spec));
  const doc = useMemo(() => JSON.parse(text) as Record<string, unknown>, [text]);
  const errors = useMemo(() => specErrors(doc), [doc]);
  const run = useSandboxRun(PLUGIN, "query", { spec: text }, { enabled: errors.length === 0 });
  const answer = useMemo(
    () => (run.data && run.data.exit_code === 0 ? readAnswer(run.data.stdout) : null),
    [run.data],
  );

  let body: ReactNode;
  if (errors.length) body = <Notice role="alert">{`This chart file does not fit the chart spec:\n${errors.join("\n")}`}</Notice>;
  else if (run.error) body = <Notice role="alert">{run.error.message}</Notice>;
  else if (run.data && run.data.exit_code !== 0)
    body = <Notice role="alert">{run.data.stderr.trim() || run.data.stdout.trim() || `exit ${run.data.exit_code}`}</Notice>;
  else if (typeof answer === "string") body = <Notice role="alert">{answer}</Notice>;
  else if (answer) body = <Plot doc={doc} answer={answer} />;
  else body = <Notice>Computing the chart in the sandbox…</Notice>;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 8px" }}>
        <button type="button" onClick={run.refetch} title="Recompute from the current data">
          Refresh
        </button>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>{body}</div>
    </div>
  );
}
