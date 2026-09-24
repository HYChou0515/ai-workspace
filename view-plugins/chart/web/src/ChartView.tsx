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

import { type EntityViewProps, isLit, useMarking, useSandboxRun, viewDocument } from "@aiws/view-sdk";

import { createChart, type Chart } from "./echarts";
import { type Answer, type Built, toOption } from "./option";
import { highlightMarking, markingLit, selectionMarking } from "./marking";
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

function Plot({
  doc,
  answer,
  marking,
  source,
}: {
  doc: Record<string, unknown>;
  answer: Answer;
  /** The named marking this view is on (#847 PR 3), or null. */
  marking: string | null;
  /** The view file, named as the `source` of what it writes. */
  source: string | null;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [selection, setSelection] = useState<Selection[]>([]);
  const keys = useMemo(() => (Array.isArray(doc.keys) ? (doc.keys as string[]) : []), [doc]);
  // Subscribed by NAME: a write to another marking does not re-render this view.
  const [entry, write] = useMarking(marking);
  // On a marking that holds something, IT decides what is lit (the platform's
  // rule, over the columns each layer carries); otherwise the spec's highlight.
  const lit = useMemo(
    () => (marking && entry ? markingLit(answer, entry.marking, isLit) : undefined),
    [marking, entry, answer],
  );
  const built: Built = useMemo(
    () => toOption(doc, answer, { gridImage: gridCanvas, ...(lit ? { lit } : {}) }),
    [doc, answer, lit],
  );
  const builtRef = useRef(built);
  builtRef.current = built;

  // What a gesture writes. Read through a ref: the ECharts handlers are bound once.
  const writeRef = useRef<(sel: Selection[]) => void>(() => {});
  // Whether this view's brush holds a selection the PERSON made. ECharts fires
  // `brushselected` with no areas whenever a brush component is (re)built —
  // every setOption does — and taking that as "cleared" would erase the marking
  // this view just wrote, re-render, rebuild the brush, and fire again.
  const brushed = useRef(false);
  writeRef.current = (sel) => {
    setSelection(sel);
    if (!marking) return;
    const values = selectionMarking(sel, answer, keys);
    if (values) write(values, source);
  };

  // The spec's `highlight:` seeds its marking on open — only an EMPTY one: a
  // marking another view already holds is the person's, not this file's.
  const seeded = useRef<string | null>(null);
  useEffect(() => {
    if (!marking || seeded.current === marking) return;
    seeded.current = marking;
    if (entry) return;
    const values = highlightMarking(answer, keys);
    if (values && Object.keys(values).length > 0) write(values, source);
  }, [marking, entry, answer, keys, write, source]);

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current);
    chartRef.current = chart;
    chart.on("brushselected", (p) => {
      const event = p as BrushSelected;
      const drawn = (event.batch?.[0]?.areas ?? []).length > 0;
      // No areas and nothing drawn by the person: the component rebuilding.
      if (!drawn && !event.batch?.[0]?.areas) return;
      if (!drawn && !brushed.current) return;
      brushed.current = drawn;
      writeRef.current(selectionFromBrush(event, builtRef.current));
    });
    chart.on("legendselectchanged", (p) =>
      writeRef.current(
        selectionFromLegend((p as { selected: Record<string, boolean> }).selected, builtRef.current),
      ),
    );
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => chart.resize());
    resize?.observe(el.current);
    return () => {
      resize?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // What the chart was last fully built from. When only the marking's lit rows
  // changed, the series are replaced and everything else — the brush the person
  // drew above all — stays, so they can still see and clear their selection.
  const drawnFrom = useRef<{ doc: unknown; answer: unknown } | null>(null);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const same = drawnFrom.current?.doc === doc && drawnFrom.current?.answer === answer;
    if (same) {
      chart.setOption(built.option, { replaceMerge: ["series"] });
      return;
    }
    drawnFrom.current = { doc, answer };
    chart.setOption(built.option, true);
    // A full setOption drops the drawn brush, and with it anything to clear.
    brushed.current = false;
    setSelection([]);
  }, [built, doc, answer]);

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

export function ChartView({ spec, path }: EntityViewProps) {
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
  else if (answer) {
    const marking = typeof doc.marking === "string" && doc.marking ? doc.marking : null;
    body = <Plot doc={doc} answer={answer} marking={marking} source={path ?? null} />;
  }
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
