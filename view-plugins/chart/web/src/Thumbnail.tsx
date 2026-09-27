/**
 * #847/#848 P6 — `view: chart`, drawn small for the chat card of a shown file.
 *
 * The same sandbox calls the live view makes, with the same arguments — so
 * the answer is one cache entry, and opening the card after it was drawn asks
 * nothing again — drawn by the same renderer: a plot is `toOption` into
 * ECharts, a gallery is the first groups in the gallery's own order painted by
 * `gallery.thumbnail`. Static: no brush, no toolbox, no tooltip, no marking,
 * and it draws once. Whatever cannot be drawn is handed to `onFail`, and the
 * host shows its plain file card instead.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { type ViewThumbnailProps, useSandboxRun, viewDocument } from "@aiws/view-sdk";

import { PLUGIN, gridCanvas, readAnswer } from "./ChartView";
import { createChart } from "./echarts";
import { Canvas } from "./FacetGallery";
import { sortedPositions, thumbnail, type FacetIndex } from "./gallery";
import { type Answer, toOption } from "./option";
import { specErrors } from "./spec";
import type { WireColumn } from "./wire";

type RunData = { stdout: string; stderr: string; exit_code: number } | undefined;
type Fail = (reason: string) => void;

/** Why a call's answer cannot be drawn, or null when it can (or is pending). */
function refusal(run: { data: RunData; error: Error | null }): string | null {
  if (run.error) return run.error.message;
  if (run.data && run.data.exit_code !== 0) return run.data.stderr.trim() || `exit ${run.data.exit_code}`;
  return null;
}

function parse<T>(data: RunData): T | null {
  if (!data || data.exit_code !== 0) return null;
  try {
    return JSON.parse(data.stdout) as T;
  } catch {
    return null;
  }
}

/** Report `reason` once it is known; `onFail` is the host's, and stable. */
function useFailWith(reason: string | null, onFail: Fail) {
  useEffect(() => {
    if (reason) onFail(reason);
  }, [reason, onFail]);
}

export function ChartThumbnail({ spec, onFail }: ViewThumbnailProps) {
  // Keyed on the text, as the live view is: `spec` is a fresh object per render.
  const text = JSON.stringify(viewDocument(spec));
  const doc = useMemo(() => JSON.parse(text) as Record<string, unknown>, [text]);
  const errors = useMemo(() => specErrors(doc), [doc]);
  useFailWith(errors.length ? errors.join("\n") : null, onFail);
  if (errors.length) return null;
  if (doc.facet !== undefined) return <GalleryThumbnail doc={doc} text={text} onFail={onFail} />;
  return <PlotThumbnail doc={doc} text={text} onFail={onFail} />;
}

function PlotThumbnail({ doc, text, onFail }: { doc: Record<string, unknown>; text: string; onFail: Fail }) {
  const run = useSandboxRun(PLUGIN, "query", { spec: text });
  const answer = useMemo(() => (run.data && run.data.exit_code === 0 ? readAnswer(run.data.stdout) : null), [run.data]);
  useFailWith(refusal(run) ?? (typeof answer === "string" ? answer : null), onFail);
  const option = useMemo(() => (answer && typeof answer !== "string" ? staticOption(doc, answer) : null), [doc, answer]);
  return option ? <StaticChart option={option} /> : null;
}

/** The live view's option with everything that answers the pointer taken out. */
function staticOption(doc: Record<string, unknown>, answer: Answer): Record<string, unknown> {
  const { option } = toOption(doc, answer, { gridImage: gridCanvas });
  const { brush: _brush, ...rest } = option;
  return { ...rest, animation: false, tooltip: { show: false }, toolbox: { show: false } };
}

/** The smallest box the live view's layout (title, legend, axes and their
 * names) fits in without drawing over itself. A smaller box gets the chart
 * laid out at this size and scaled down, as a picture of the view would be. */
const MIN_W = 320;
const MIN_H = 200;

function StaticChart({ option }: { option: Record<string, unknown> }) {
  const box = useRef<HTMLDivElement>(null);
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!box.current || !el.current) return;
    const width = box.current.clientWidth || FALLBACK.width;
    const height = box.current.clientHeight || FALLBACK.height;
    const scale = Math.min(1, width / MIN_W, height / MIN_H);
    // Set before ECharts measures its element, and never again: it is static.
    el.current.style.width = `${width / scale}px`;
    el.current.style.height = `${height / scale}px`;
    if (scale < 1) el.current.style.transform = `scale(${scale})`;
    // Its pixels are the screen's (#847/#848 PR 5 P29): at the screen's ratio
    // the transform resampled the canvas smoothly and a grid's cells blurred.
    // What is left (a fraction of a pixel) is composited nearest-neighbour.
    const chart = createChart(el.current, { devicePixelRatio: window.devicePixelRatio * scale });
    chart.setOption(option, true);
    return () => chart.dispose();
  }, [option]);
  return (
    <div ref={box} style={{ width: "100%", height: "100%", overflow: "hidden" }}>
      <div ref={el} style={{ transformOrigin: "0 0", imageRendering: "pixelated" }} />
    </div>
  );
}

/** A tile is at least this many px across; the box decides how many fit. */
const TILE_MIN = 40;
const GAP = 4;
/** The box assumed when it cannot be measured (it has no layout yet). */
const FALLBACK = { width: 260, height: 170 };

function GalleryThumbnail({ doc, text, onFail }: { doc: Record<string, unknown>; text: string; onFail: Fail }) {
  const facet = doc.facet as { sort?: { field: string; order?: "ascending" | "descending" } };
  const encoding = doc.encoding as { color?: { scale?: { scheme?: "sequential" | "diverging" } } };
  const scheme = encoding.color?.scale?.scheme ?? "sequential";

  // How many tiles fit, measured once: the thumbnail does not follow a resize.
  const box = useRef<HTMLDivElement>(null);
  const [grid, setGrid] = useState<{ cols: number; rows: number; size: number } | null>(null);
  useLayoutEffect(() => {
    const width = box.current?.clientWidth || FALLBACK.width;
    const height = box.current?.clientHeight || FALLBACK.height;
    const cols = Math.max(1, Math.floor((width + GAP) / (TILE_MIN + GAP)));
    const rows = Math.max(1, Math.floor((height + GAP) / (TILE_MIN + GAP)));
    const size = Math.floor(Math.min((width - GAP * (cols - 1)) / cols, (height - GAP * (rows - 1)) / rows));
    setGrid({ cols, rows, size });
  }, []);

  // The gallery's own first calls (epoch 0), so opening it asks nothing again.
  const build = useSandboxRun(PLUGIN, "facet_build", { spec: text, epoch: 0 });
  const built = useMemo(() => parse<{ key: string }>(build.data), [build.data]);
  const index = useSandboxRun(PLUGIN, "facet_index", { key: built?.key ?? "", epoch: 0 }, { enabled: !!built });
  const idx = useMemo(() => parse<FacetIndex>(index.data), [index.data]);
  const positions = useMemo(() => {
    if (!idx || !grid) return [];
    const sort = facet.sort ? { field: facet.sort.field, order: facet.sort.order ?? "ascending" } : null;
    return sortedPositions(idx, sort).slice(0, grid.cols * grid.rows);
  }, [idx, grid, facet.sort]);
  const page = useSandboxRun(
    PLUGIN,
    "facet_page",
    { key: built?.key ?? "", build: idx?.build ?? "", positions, epoch: 0 },
    { enabled: !!idx && positions.length > 0 },
  );
  const columns = useMemo(() => parse<{ groups: WireColumn[] }>(page.data)?.groups ?? null, [page.data]);
  useFailWith(refusal(build) ?? refusal(index) ?? refusal(page), onFail);

  const images = useMemo(
    () => (idx && columns ? columns.map((column) => thumbnail(idx, column, scheme)) : null),
    [idx, columns, scheme],
  );

  return (
    <div
      ref={box}
      style={{
        width: "100%",
        height: "100%",
        display: "grid",
        gridTemplateColumns: `repeat(${grid?.cols ?? 1}, ${grid?.size ?? TILE_MIN}px)`,
        gap: GAP,
        alignContent: "start",
        overflow: "hidden",
      }}
    >
      {images && grid && images.map((image, k) => <Canvas key={positions[k]} image={image} size={grid.size} />)}
    </div>
  );
}
