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
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { type EntityViewProps, isLit, useMarking, useSandboxRun, viewDocument } from "@aiws/view-sdk";

import { createChart, type Chart } from "./echarts";
import { FacetGallery } from "./FacetGallery";
import { DIM_OPACITY } from "./highlight";
import { type Answer, type Built, compactAt, type Layout, measuredFields, toOption, withLayout } from "./option";
import { highlightMarking, markedBy, markedCount, markingLit, type MarkingValues, selectionMarking, stillWritten } from "./marking";
import { type Cells, type RasterImage, upscale } from "./raster";
import {
  type BrushSelected,
  type ClickParams,
  ownSelectionLit,
  type Selection,
  selectionFromBrush,
  selectionFromClick,
  selectionFromLegend,
} from "./selection";
import { specErrors } from "./spec";
import { viewCall } from "./viewCall";

export const PLUGIN = "chart";
const FORMAT = 1;
/** What a mark outside a brush that writes nothing is drawn with: its own
 * colour, dimmed as a marking dims (#847/#848 PR 5 P44 row 37). ECharts'
 * default (component/brush/BrushModel.js DEFAULT_OUT_OF_BRUSH_COLOR) paints it
 * #ddd, and a scatter over bars so painted vanished into them. Stated, so a
 * chart that stops lighting by its marking gets it back. */
const OUT_OF_BRUSH = { opacity: DIM_OPACITY };

function Notice({ role = "status", children }: { role?: "status" | "alert"; children: ReactNode }) {
  const color = role === "alert" ? "var(--err)" : "var(--text-paper-d)";
  return (
    <div role={role} style={{ padding: 12, color, whiteSpace: "pre-wrap" }}>
      {children}
    </div>
  );
}

/** The last canvas painted for each image: renderItem asks on every redraw. */
const painted = new WeakMap<RasterImage, HTMLCanvasElement>();

/** The grid's pixels as a canvas ECharts can draw, `size` device pixels
 * across -- the box it is drawn in, so it is drawn 1:1 -- each cell whole
 * pixels of its colour (#847/#848 PR 5 P29). */
export function gridCanvas(
  { image }: { cells: Cells; image: RasterImage },
  size: { width: number; height: number },
): HTMLCanvasElement {
  const last = painted.get(image);
  if (last && last.width === size.width && last.height === size.height) return last;
  const big = upscale(image, size.width, size.height);
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.getContext("2d")?.putImageData(new ImageData(big.data as Uint8ClampedArray<ArrayBuffer>, big.width, big.height), 0, 0);
  painted.set(image, canvas);
  return canvas;
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
  // Per layer, the fields sent holding an aggregate: never a key (PR 5 P32),
  // as the answer says (a stack's sum too, P40 row 18).
  const measured = useMemo(() => measuredFields(answer), [answer]);
  // Subscribed by NAME: a write to another marking does not re-render this view.
  const [entry, write] = useMarking(marking);
  // On a marking that holds something, IT decides what is lit (the platform's
  // rule, over the columns each layer carries); otherwise the spec's highlight.
  // On an EMPTY marking nothing is lit: every view on it draws undimmed, rather
  // than each falling back to its own `highlight:` and disagreeing.
  // A selection that writes nothing -- no marking, or one this view cannot
  // write (no `keys:`) -- lights a GRID it took cells of or a PIE slice it
  // clicked itself: a grid is one raster image, which ECharts' brush styling
  // cannot dim, and a click ECharts does not style, so without this either
  // showed only a count (#847/#848 P18, PR 5 P30, P34).
  // What this view's latest selection wrote, to which marking and as which
  // source; null when it wrote nothing (no marking -- detached --, no
  // `keys:`). State, not a ref: whether the selection went to the marking is
  // read from it on render (below).
  const [wrote, setWrote] = useState<{ on: string; source: string | null; values: MarkingValues } | null>(null);
  // Whether the person's selection here went to the marking (#847/#848 PR 5
  // P29). Then the marking lights this chart as it lights every other view,
  // and ECharts' own brush visual -- every point outside the box greyed -- is
  // off: over two columns the marking lights every combination of their
  // values, and the brushed chart showed 16 lit where the tables showed 27.
  // A selection that writes nothing (no marking, no `keys:`) keeps it.
  // Read from what the selection WROTE (P37 row 13): the marking it wrote to
  // is the one the chart is on now. One made detached wrote nothing, and one
  // written to another marking is that marking's -- a flag set at the write
  // and tied to no marking said "· by <columns>" of the marking the chart
  // moved to, and kept the brush visual off.
  const toMarking = wrote !== null && wrote.on === marking;
  const lit = useMemo(() => {
    const own = toMarking ? undefined : ownSelectionLit(answer, selection);
    if (own || !marking) return own;
    return entry ? markingLit(answer, entry.marking, isLit, measured) : answer.layers.map(() => null);
  }, [marking, toMarking, entry, answer, selection, measured]);
  // How the chart is laid out (#847/#848 PR 5 P31, P34): compact or not, read
  // from the width its host is given by the observer that resizes it, and --
  // compact -- its height, of which the plot keeps half. It is not part of
  // what the option is built from: a switch of layout is merged into the
  // drawn chart as the layout alone (`Built.layout`), never a rebuild.
  const [layout, setLayout] = useState<Layout>({ compact: false });
  const built: Built = useMemo(
    () => toOption(doc, answer, { gridImage: gridCanvas, ...(lit ? { lit } : {}) }),
    [doc, answer, lit],
  );
  const builtRef = useRef(built);
  builtRef.current = built;
  const laid = useMemo(() => built.layout(layout), [built, layout]);
  // Whether ECharts' own brush visual is off (see `toMarking`).
  const brushOff = toMarking;
  // (every chart has a brush: a pie alone has only its ✕, PR 5 P31)
  const option = useMemo(() => {
    const full = withLayout(built, layout);
    return { ...full, brush: { ...(full.brush as object), outOfBrush: brushOff ? { colorAlpha: 1 } : OUT_OF_BRUSH } };
  }, [built, layout, brushOff]);
  const notes = [...built.notes, ...laid.notes];

  // What a gesture writes, and what it wrote to the marking (null: nothing --
  // no marking, or one this view cannot write). Read through a ref: the
  // ECharts handlers are bound once.
  const writeRef = useRef<(sel: Selection[]) => MarkingValues | null>(() => null);
  // Whether this view's brush holds a selection the PERSON made. ECharts fires
  // `brushselected` with no areas whenever a brush component is (re)built —
  // every setOption does — and taking that as "cleared" would erase the marking
  // this view just wrote, re-render, rebuild the brush, and fire again.
  const brushed = useRef(false);
  // The pie slice ("seriesIndex:dataIndex") a click picked (#847/#848 PR 5
  // P34): a click toggles only what it wrote. Its second click, or a click on
  // empty space, clears it; once any other write replaced what it wrote to
  // the marking, the pick is gone (P35 row 9, below) -- empty space clears
  // nothing, and the slice is picked afresh. A pick that wrote nothing is
  // this view's alone and stays its to clear.
  const clicked = useRef<string | null>(null);
  writeRef.current = (sel) => {
    // ECharts re-reports the areas it holds: the same rows keep the same state,
    // or a grid lit by its own selection would redraw on every report
    setSelection((prev) => (JSON.stringify(prev) === JSON.stringify(sel) ? prev : sel));
    if (!marking) {
      setWrote(null);
      return null;
    }
    const values = selectionMarking(sel, answer, keys, measured);
    // Only a write the store took is one another view's can replace (PR 5
    // P40 row 19): with no store (a standalone preview) it went nowhere, and
    // the selection is this chart's own -- as a detached chart's is.
    const took = values !== null && write(values, source);
    setWrote(took && values ? { on: marking, source, values } : null);
    return took ? values : null;
  };

  // The marking is what a linked chart shows (#847/#848 PR 5 P35 row 9). Once
  // another write replaced what this view's selection wrote -- another view's,
  // the same values from another view, a clear -- its own selection goes: the
  // count, the brush's box, the pie's pick, the legend's hidden entries. They
  // said what the marking no longer holds. This view's own write is still
  // written (`stillWritten`), so it drops nothing; a selection that wrote
  // nothing is this view's alone, and stays; and a view detached from the
  // marking, or moved to another, shows its own again (P29) -- what it wrote
  // is the old marking's. Its write is compared as the source it was made as
  // (P37 row 15): the view file renamed under the chart is not another view.
  // A layout effect: the drop is done before the next click can find the pick
  // it drops.
  useLayoutEffect(() => {
    const mine = wrote;
    if (mine === null || mine.on !== marking || stillWritten(entry, mine.values, mine.source)) return;
    clicked.current = null;
    // first, so the empty `brushselected` the clear fires is not taken for
    // the person clearing
    brushed.current = false;
    const chart = chartRef.current;
    if (chart) {
      chart.dispatchAction({ type: "brush", areas: [] });
      // (fires no `legendselectchanged`: bringing them back writes nothing;
      // with none hidden, or no legend, it changes nothing)
      chart.dispatchAction({ type: "legendAllSelect" });
    }
    setSelection([]);
  }, [entry, wrote, marking]);

  // The spec's `highlight:` seeds its marking on open — only an EMPTY one: a
  // marking another view already holds is the person's, not this file's.
  // Once per open: re-attaching through the header later must not re-seed a
  // marking the person has since cleared. `ifEmpty` is checked by the store at
  // write time — two views opened together both RENDERED an empty marking.
  const seeded = useRef(false);
  useEffect(() => {
    if (!marking || seeded.current) return;
    seeded.current = true;
    const values = highlightMarking(answer, keys, measured);
    if (values && Object.keys(values).length > 0) write(values, source, { ifEmpty: true });
  }, [marking, answer, keys, measured, write, source]);

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
      // a slice a click picked is cleared too: the next click on it picks it
      // again, rather than taking it for a second click (PR 5 P31)
      clicked.current = null;
      writeRef.current([]);
    });
    chart.on("legendselectchanged", (p) => {
      // the legend's choice replaces a clicked slice's: empty space is not to clear it
      clicked.current = null;
      writeRef.current(
        selectionFromLegend((p as { selected: Record<string, boolean> }).selected, builtRef.current),
      );
    });
    // A pie's slice is picked by clicking it (#847/#848 PR 5 P30); the same
    // slice again, or empty space, clears what the click picked -- while it is
    // still picked: another view's write drops the pick (P34, P35 row 9), and
    // a selection another view wrote is not this click's to clear.
    chart.on("click", (p) => {
      const params = p as ClickParams;
      const sel = selectionFromClick(params, builtRef.current);
      if (sel.length === 0) return;
      const key = `${params.seriesIndex}:${params.dataIndex}`;
      if (clicked.current === key) {
        clicked.current = null;
        writeRef.current([]);
        return;
      }
      writeRef.current(sel);
      clicked.current = key;
    });
    chart.getZr().on("click", (e) => {
      if (e.target || clicked.current === null) return;
      clicked.current = null;
      writeRef.current([]);
    });
    const resize =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver((entries) => {
            chart.resize();
            const box = entries[0]?.contentRect;
            // 0 px is no width -- a pane hidden (display: none) -- not a
            // narrow one: the layout stays as it was (P34)
            if (!box || box.width <= 0) return;
            const compact = compactAt(box.width);
            // the height matters only compact; a wide chart's is not kept, so
            // its every pixel of height is no new layout
            const height = compact && box.height > 0 ? Math.round(box.height) : undefined;
            setLayout((prev) => (prev.compact === compact && prev.height === height ? prev : { compact, height }));
          });
    resize?.observe(el.current);
    return () => {
      resize?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // What the chart was last drawn from. Only a change of doc or answer is a
  // full rebuild (#847/#848 PR 5 P34). When only the marking's lit rows
  // changed, the series are replaced and everything else — the brush the
  // person drew above all — stays, so they can still see and clear their
  // selection. When only the layout changed, the layout alone is merged in:
  // every key either layout sets is set by both (`Built.layout`), so nothing
  // of the other is left over, and the brush, the count, the legend's hidden
  // entries and a pie's or grid's own pick all stay.
  // (`brushOff` changes only with `toMarking`, which `lit` -- and so `built`
  // -- is recomputed from: a new `built` covers it.)
  const drawn = useRef<{ doc: unknown; answer: unknown; built: Built; laid: string } | null>(null);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const was = drawn.current;
    const laidText = JSON.stringify(laid.option);
    drawn.current = { doc, answer, built, laid: laidText };
    if (was?.doc === doc && was.answer === answer) {
      if (was.built !== built) chart.setOption(option, { replaceMerge: ["series"] });
      else if (was.laid !== laidText) chart.setOption(laid.option);
      return;
    }
    chart.setOption(option, true);
    // A full setOption drops the drawn brush, and with it anything to clear;
    // and what a click wrote is forgotten, or the next click on its slice
    // would be taken for a second one and clear it.
    brushed.current = false;
    clicked.current = null;
    setSelection([]);
  }, [option, laid, doc, answer, built]);

  // Beside "by <columns>", what went to the marking: a stack's segment that
  // wrote nothing is not counted there (P43); otherwise every row picked.
  const marks = !!entry && toMarking;
  const count = marks
    ? markedCount(selection, answer, keys, measured)
    : selection.reduce((n, s) => n + s.rows.length, 0);
  // On a marking, which columns it marks by (P27): over two columns it lights
  // every combination of their values, more than the rows picked here. Said
  // only of a selection that went to the marking: one that wrote nothing (no
  // `keys:`) picked just its own rows, which the marking's columns -- another
  // view's write -- have nothing to do with (PR 5 P36 row 10).
  const selected = count > 0 ? `${count} selected${marks && entry ? ` · ${markedBy(entry.marking)}` : ""}` : null;
  return (
    // Takes the height its pane gives it (#847/#848 PR 5 P13); a fixed 360 px
    // made every shorter pane scroll. 160 px is the least a plot reads at.
    <div style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", minHeight: 160 }}>
      {/* Always there, one line high: added only once something was selected,
          it pushed the chart down under the pointer (#847/#848 P18). */}
      <div
        title={[...(selected ? [selected] : []), ...notes].join(" · ")}
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
        {/* The count comes first and keeps its width beside a note (P44 row
            36: at 390 wide the note left "6 selected · …", hiding "by
            item"); only a line too narrow for the count alone ends it in an
            ellipsis (P27: a 155 px panel cut "· by group, item" off with
            nothing to say so). The line's title holds all of it. */}
        {selected && (
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", minWidth: 0, flexShrink: 0, maxWidth: "100%" }}>
            {selected}
          </span>
        )}
        {/* a note yields: too long for what the count leaves, it ends in an
            ellipsis, its whole text on hover (P42 row 29: at 390 wide the
            stack's note lost its field names) */}
        {notes.map((n) => (
          <span key={n} title={n} style={{ overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>
            {n}
          </span>
        ))}
      </div>
      {/* composited nearest-neighbour (P29): at a fractional pixel ratio
          the browser scales the canvas by a fraction of a pixel, which,
          smoothed, blended a grid's cells at every edge */}
      <div ref={el} style={{ flex: 1, minHeight: 0, imageRendering: "pixelated" }} />
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
        <button
          type="button"
          className="btn"
          data-variant="ghost"
          data-size="sm"
          onClick={run.refetch}
          title="Recompute from the current data"
        >
          Refresh
        </button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", minHeight: 0 }}>{body}</div>
    </div>
  );
}
