/**
 * #848 P6/P7: a `facet:` spec as a gallery of small grid maps, one per group.
 *
 * - `facet_build {spec}` builds (or reuses) the sandbox cache; `facet_index
 *   {key}` gives every group's key and sort values. Sorting is over that index
 *   in hand, so flipping the order costs no rebuild.
 * - Only the pages near the viewport mount, and each asks `facet_page` for its
 *   run of sorted positions — 1000+ groups never load at once.
 * - A thumbnail is painted by `gallery.thumbnail`, the same calls the full grid
 *   makes, so the same cells are the same pixels (Q13).
 * - A selection is a range of SORTED positions (shift-click, or ranks a–b); it
 *   writes every group in it to the marking, loaded or not (P7). Groups the
 *   marking holds are lit by the platform's `isLit`.
 * - Exit 3 (no usable cache) rebuilds; exit 4 (the cache was rebuilt since the
 *   index) refetches the index.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { isLit, useMarking, useSandboxRun } from "@aiws/view-sdk";

import { groupsLit, groupsPerPage, rangeMarking, sortedPositions, thumbnail, type FacetIndex } from "./gallery";
import type { RasterImage } from "./raster";
import { decodeColumn, type WireColumn } from "./wire";

const PLUGIN = "chart";
const UNUSABLE = 3;
const STALE = 4;
const TILE = 112; // px, a thumbnail and its label
const THUMB = 96;
const FALLBACK_VIEWPORT = { width: 800, height: 600 };

type RunData = { stdout: string; stderr: string; exit_code: number } | undefined;

function parse<T>(data: RunData): T | null {
  if (!data || data.exit_code !== 0) return null;
  try {
    return JSON.parse(data.stdout) as T;
  } catch {
    return null;
  }
}

function Notice({ role = "status", children }: { role?: "status" | "alert"; children: ReactNode }) {
  const color = role === "alert" ? "var(--err)" : "var(--text-paper-d)";
  return (
    <div role={role} style={{ padding: 12, color, whiteSpace: "pre-wrap" }}>
      {children}
    </div>
  );
}

function Canvas({ image, size, onHover }: { image: RasterImage; size: number; onHover?: (cell: number | null) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || image.width === 0 || image.height === 0) return;
    canvas.width = image.width;
    canvas.height = image.height;
    ctx.putImageData(new ImageData(new Uint8ClampedArray(image.data), image.width, image.height), 0, 0);
  }, [image]);
  return (
    <canvas
      ref={ref}
      style={{ width: size, height: size, imageRendering: "pixelated", background: "var(--bg-sunken, #0000)" }}
      onMouseMove={
        onHover &&
        ((e) => {
          const r = e.currentTarget.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) return onHover(null);
          const col = Math.floor(((e.clientX - r.left) / r.width) * image.width);
          const row = Math.floor(((e.clientY - r.top) / r.height) * image.height);
          onHover(row * image.width + col);
        })
      }
      onMouseLeave={onHover && (() => onHover(null))}
    />
  );
}

type Shared = {
  index: FacetIndex;
  cacheKey: string;
  scheme: "sequential" | "diverging";
  columns: number;
  lit: boolean[] | null;
  selected: ReadonlySet<number>;
  onPick: (position: number, shift: boolean) => void;
  onEnlarge: (position: number) => void;
  onStale: () => void;
  onUnusable: () => void;
};

function Page({ positions, first, shared }: { positions: number[]; first: number; shared: Shared }) {
  const { index, cacheKey } = shared;
  const run = useSandboxRun(PLUGIN, "facet_page", { key: cacheKey, build: index.build, positions });
  const code = run.data?.exit_code;
  useEffect(() => {
    if (code === STALE) shared.onStale();
    else if (code === UNUSABLE) shared.onUnusable();
  }, [code, shared]);
  const page = parse<{ groups: WireColumn[] }>(run.data);
  return (
    <>
      {positions.map((p, k) => {
        const rank = first + k;
        const column = page?.groups[k];
        const lit = shared.lit ? shared.lit[p] : undefined;
        return (
          <div
            key={p}
            style={{
              position: "absolute",
              left: (rank % shared.columns) * TILE,
              top: Math.floor(rank / shared.columns) * TILE,
              width: TILE - 8,
              outline: shared.selected.has(p) ? "2px solid var(--accent, #4a8)" : undefined,
            }}
          >
            <div
              role="button"
              tabIndex={0}
              aria-label={`group ${index.groups[p].key.join(" · ")}`}
              onClick={(e) => shared.onPick(p, e.shiftKey)}
              onKeyDown={(e) => e.key === "Enter" && shared.onPick(p, e.shiftKey)}
              style={{ cursor: "pointer" }}
            >
              {column ? (
                <Canvas image={thumbnail(index, column, shared.scheme, lit)} size={THUMB} />
              ) : (
                <div style={{ width: THUMB, height: THUMB }} />
              )}
            </div>
            <div style={{ display: "flex", fontSize: 11, gap: 4 }}>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {index.groups[p].key.join(" · ")}
              </span>
              <button type="button" aria-label="enlarge" onClick={() => shared.onEnlarge(p)}>
                ⤢
              </button>
            </div>
          </div>
        );
      })}
    </>
  );
}

function Enlarged({
  shared,
  position,
  onClose,
}: {
  shared: Shared;
  position: number;
  onClose: () => void;
}) {
  const { index, cacheKey } = shared;
  const page = useSandboxRun(PLUGIN, "facet_page", { key: cacheKey, build: index.build, positions: [position] });
  const exact = useSandboxRun(PLUGIN, "facet_exact", { key: cacheKey, build: index.build, position });
  const column = parse<{ groups: WireColumn[] }>(page.data)?.groups[0];
  const values = useMemo(() => {
    const wire = parse<WireColumn>(exact.data);
    if (!wire) return null;
    const col = decodeColumn(wire);
    return Array.from({ length: index.cells }, (_, i) => col.value(i));
  }, [exact.data, index.cells]);
  const [cell, setCell] = useState<number | null>(null);
  const image = column ? thumbnail(index, column, shared.scheme) : null;
  // a lattice cell -> the cache cell it shows: invert the lattice's placement
  const hovered = useMemo(() => {
    if (cell === null || !image || !values) return null;
    const x = index.layout.x;
    const y = index.layout.y;
    const xs = [...new Set(x)];
    const ys = [...new Set(y)];
    const col = cell % image.width;
    const row = Math.floor(cell / image.width);
    const at = x.findIndex((v, i) => xs.indexOf(v) === col && ys.length - 1 - ys.indexOf(y[i]) === row);
    return at >= 0 ? values[at] : null;
  }, [cell, image, values, index.layout]);
  return (
    <div role="dialog" aria-label={`group ${index.groups[position].key.join(" · ")}`} style={{ position: "absolute", inset: 0, background: "var(--bg, #fff)", padding: 12, zIndex: 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{index.groups[position].key.join(" · ")}</strong>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {image ? <Canvas image={image} size={384} onHover={setCell} /> : <Notice>Loading…</Notice>}
      <div style={{ fontSize: 12 }}>{hovered === null ? "Hover a cell for its exact value" : `value: ${String(hovered)}`}</div>
    </div>
  );
}

export function FacetGallery({
  doc,
  text,
  marking,
  source,
}: {
  doc: Record<string, unknown>;
  /** The spec's text, as `facet_build` takes it. */
  text: string;
  marking: string | null;
  source: string | null;
}) {
  const facet = doc.facet as { sort?: { field: string; order?: "ascending" | "descending" } };
  const encoding = doc.encoding as { color?: { scale?: { scheme?: "sequential" | "diverging" } } };
  const scheme = encoding.color?.scale?.scheme ?? "sequential";

  const build = useSandboxRun(PLUGIN, "facet_build", { spec: text });
  // parsed once per answer: a fresh object every render would re-run every
  // effect and memo keyed on it
  const built = useMemo(() => parse<{ key: string; build: string; groups: number }>(build.data), [build.data]);
  const index = useSandboxRun(PLUGIN, "facet_index", { key: built?.key ?? "" }, { enabled: !!built });
  const idx = useMemo(() => parse<FacetIndex>(index.data), [index.data]);
  const indexCode = index.data?.exit_code;
  useEffect(() => {
    if (indexCode === UNUSABLE) build.refetch();
  }, [indexCode, build]);

  const [order, setOrder] = useState(facet.sort?.order ?? "ascending");
  const sorted = useMemo(
    () => (idx ? sortedPositions(idx, facet.sort ? { field: facet.sort.field, order } : null) : []),
    [idx, facet.sort, order],
  );

  const [entry, write] = useMarking(marking);
  const lit = useMemo(() => (idx && entry ? groupsLit(idx, entry.marking, isLit) : null), [idx, entry]);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [enlarged, setEnlarged] = useState<number | null>(null);

  const scroller = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({ top: 0, ...FALLBACK_VIEWPORT });
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => {
      const next = {
        top: el.scrollTop,
        width: el.clientWidth || FALLBACK_VIEWPORT.width,
        height: el.clientHeight || FALLBACK_VIEWPORT.height,
      };
      setViewport((v) => (v.top === next.top && v.width === next.width && v.height === next.height ? v : next));
    };
    measure();
    el.addEventListener("scroll", measure);
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resize?.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      resize?.disconnect();
    };
  }, [idx]);

  if (build.error) return <Notice role="alert">{build.error.message}</Notice>;
  if (build.data && build.data.exit_code !== 0)
    return <Notice role="alert">{build.data.stderr.trim() || `exit ${build.data.exit_code}`}</Notice>;
  if (!built) return <Notice>Building the gallery in the sandbox…</Notice>;
  if (index.data && index.data.exit_code !== 0 && indexCode !== UNUSABLE)
    return <Notice role="alert">{index.data.stderr.trim() || `exit ${index.data.exit_code}`}</Notice>;
  if (!idx) return <Notice>Opening the gallery…</Notice>;

  const writeRange = (positions: number[]) => {
    setSelected(new Set(positions));
    if (marking) write(rangeMarking(idx, positions), source);
  };
  const shared: Shared = {
    index: idx,
    cacheKey: built.key,
    scheme,
    columns: Math.max(1, Math.floor(viewport.width / TILE)),
    lit,
    selected,
    onPick: (p, shift) => {
      const rank = sorted.indexOf(p);
      if (shift && anchor !== null) {
        const a = sorted.indexOf(anchor);
        writeRange(sorted.slice(Math.min(a, rank), Math.max(a, rank) + 1));
      } else {
        setAnchor(p);
        writeRange([p]);
      }
    },
    onEnlarge: setEnlarged,
    onStale: index.refetch,
    onUnusable: build.refetch,
  };

  const perPage = groupsPerPage(idx.cells);
  const rows = Math.ceil(sorted.length / shared.columns);
  const firstRank = Math.floor(viewport.top / TILE) * shared.columns;
  const lastRank = Math.ceil((viewport.top + viewport.height) / TILE) * shared.columns;
  const firstPage = Math.max(0, Math.floor(firstRank / perPage));
  const lastPage = Math.min(Math.ceil(sorted.length / perPage) - 1, Math.floor(lastRank / perPage));
  const pages = [];
  for (let n = firstPage; n <= lastPage; n++) pages.push(n);
  const marked = lit ? lit.filter(Boolean).length : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", position: "relative" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "4px 12px", fontSize: 12 }}>
        <span>{`${sorted.length} groups`}</span>
        {lit && <span>{`${marked} of ${sorted.length} marked`}</span>}
        {facet.sort && (
          <button type="button" onClick={() => setOrder(order === "ascending" ? "descending" : "ascending")}>
            {`${facet.sort.field} ${order}`}
          </button>
        )}
        <label>
          from rank <input value={from} onChange={(e) => setFrom(e.target.value)} size={4} />
        </label>
        <label>
          to rank <input value={to} onChange={(e) => setTo(e.target.value)} size={4} />
        </label>
        <button
          type="button"
          onClick={() => {
            const a = Math.max(1, Number.parseInt(from, 10) || 1);
            const b = Math.min(sorted.length, Number.parseInt(to, 10) || a);
            if (b >= a) writeRange(sorted.slice(a - 1, b));
          }}
        >
          Select ranks
        </button>
        <button type="button" onClick={() => writeRange([])}>
          Clear selection
        </button>
      </div>
      <div ref={scroller} style={{ flex: 1, overflow: "auto", position: "relative" }}>
        <div style={{ position: "relative", height: rows * TILE }}>
          {pages.map((n) => (
            <Page key={n} positions={sorted.slice(n * perPage, (n + 1) * perPage)} first={n * perPage} shared={shared} />
          ))}
        </div>
      </div>
      {enlarged !== null && <Enlarged shared={shared} position={enlarged} onClose={() => setEnlarged(null)} />}
    </div>
  );
}
