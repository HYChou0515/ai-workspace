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
import { FacetGallery } from "./FacetGallery";
import { type Answer, type Built, toOption } from "./option";
import { highlightMarking, markingLit, selectionMarking } from "./marking";
import type { Cells, RasterImage } from "./raster";
import { type BrushSelected, gridSelectionLit, type Selection, selectionFromBrush, selectionFromLegend } from "./selection";
import { specErrors } from "./spec";
import { viewCall } from "./viewCall";

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
export function gridCanvas({ image }: { cells: Cells; image: RasterImage }): HTMLCanvasElement {
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

export function readAnswer(stdout: string): Answer | string {
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
  // On an EMPTY marking nothing is lit: every view on it draws undimmed, rather
  // than each falling back to its own `highlight:` and disagreeing.
  // With no marking, the view's own selection lights a GRID it took cells of:
  // a grid is one raster image, which ECharts' brush styling cannot dim, so
  // without this a lasso there showed only a count (#847/#848 P18).
  const lit = useMemo(
    () =>
      marking
        ? entry
          ? markingLit(answer, entry.marking, isLit)
          : answer.layers.map(() => null)
        : gridSelectionLit(answer, selection),
    [marking, entry, answer, selection],
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
    // ECharts re-reports the areas it holds: the same rows keep the same state,
    // or a grid lit by its own selection would redraw on every report
    setSelection((prev) => (JSON.stringify(prev) === JSON.stringify(sel) ? prev : sel));
    if (!marking) return;
    const values = selectionMarking(sel, answer, keys);
    if (values) write(values, source);
  };

  // The spec's `highlight:` seeds its marking on open — only an EMPTY one: a
  // marking another view already holds is the person's, not this file's.
  // Once per open: re-attaching through the header later must not re-seed a
  // marking the person has since cleared. `ifEmpty` is checked by the store at
  // write time — two views opened together both RENDERED an empty marking.
  const seeded = useRef(false);
  useEffect(() => {
    if (!marking || seeded.current) return;
    seeded.current = true;
    const values = highlightMarking(answer, keys);
    if (values && Object.keys(values).length > 0) write(values, source, { ifEmpty: true });
  }, [marking, answer, keys, write, source]);

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
    // The toolbox's ✕ dispatches `brush` with `command: "clear"` (echarts
    // toolbox/feature/Brush.js); a rebuild dispatches nothing. So the ✕ is the
    // person clearing, whatever the marking holds -- a seed from `highlight:`
    // too, which the empty brushselected after it cannot clear (nothing was
    // brushed here) (#847/#848 P17).
    chart.on("brush", (p) => {
      if ((p as { command?: string }).command !== "clear") return;
      writeRef.current([]);
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
    // Takes the height its pane gives it (#847/#848 PR 5 P13); a fixed 360 px
    // made every shorter pane scroll. 160 px is the least a plot reads at.
    <div style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", minHeight: 160 }}>
      {/* Always there, one line high: added only once something was selected,
          it pushed the chart down under the pointer (#847/#848 P18). */}
      <div
        title={[...built.notes, ...(count > 0 ? [`${count} selected`] : [])].join(" · ")}
        style={{
          display: "flex",
          gap: 12,
          height: 20,
          lineHeight: "20px",
          padding: "0 12px",
          overflow: "hidden",
          whiteSpace: "nowrap",
          fontSize: 12,
          color: "var(--text-paper-d)",
        }}
      >
        {built.notes.map((n) => (
          <span key={n}>{n}</span>
        ))}
        {count > 0 && <span>{count} selected</span>}
      </div>
      <div ref={el} style={{ flex: 1, minHeight: 0 }} />
    </div>
  );
}

export function ChartView({ spec, path, marking: chosen }: EntityViewProps) {
  // Keyed on the text: the container rebuilds `spec` on every render.
  const text = JSON.stringify(viewDocument(spec));
  const doc = useMemo(() => JSON.parse(text) as Record<string, unknown>, [text]);
  const errors = useMemo(() => specErrors(doc), [doc]);
  // A `facet:` spec is a gallery (#848): it builds a cache instead of a query.
  const faceted = doc.facet !== undefined;
  const call = useMemo(() => viewCall(text, path), [text, path]);
  const run = useSandboxRun(PLUGIN, "query", call, { enabled: errors.length === 0 && !faceted });
  const answer = useMemo(
    () => (run.data && run.data.exit_code === 0 ? readAnswer(run.data.stdout) : null),
    [run.data],
  );

  // The header's choice (#847 P3) when the platform manages it; else the file's.
  const fromFile = typeof doc.marking === "string" && doc.marking ? doc.marking : null;
  const marking = chosen !== undefined ? chosen : fromFile;

  let body: ReactNode;
  if (errors.length) body = <Notice role="alert">{`This chart file does not fit the chart spec:\n${errors.join("\n")}`}</Notice>;
  // keyed on the text: an edited spec is a new gallery -- its own epoch, and
  // not the old one's having given up
  else if (faceted) body = <FacetGallery key={text} doc={doc} text={text} marking={marking} source={path ?? null} />;
  else if (run.error) body = <Notice role="alert">{run.error.message}</Notice>;
  else if (run.data && run.data.exit_code !== 0)
    body = <Notice role="alert">{run.data.stderr.trim() || run.data.stdout.trim() || `exit ${run.data.exit_code}`}</Notice>;
  else if (typeof answer === "string") body = <Notice role="alert">{answer}</Notice>;
  else if (answer) body = <Plot doc={doc} answer={answer} marking={marking} source={path ?? null} />;
  else body = <Notice>Computing the chart in the sandbox…</Notice>;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", minHeight: 0 }}>
      <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 8px" }}>
        <button type="button" onClick={run.refetch} title="Recompute from the current data">
          Refresh
        </button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", minHeight: 0 }}>{body}</div>
    </div>
  );
}
