/**
 * #848 P6/P7: a `facet:` spec as a gallery of small grid maps, one per group.
 *
 * - `facet_build {spec}` builds (or reuses) the sandbox cache; `facet_index
 *   {key}` gives every group's key and sort values. Sorting is over that index
 *   in hand, so flipping the order costs no rebuild.
 * - Only the pages within a screen of the viewport mount, and each asks
 *   `facet_page` for its run of sorted positions — 1000+ groups never load at
 *   once, and the next page is on its way before it scrolls in.
 * - A thumbnail is painted by `gallery.thumbnail`, the same calls the full grid
 *   makes, so the same cells are the same pixels (Q13). An enlarged group reads
 *   the exact value under the pointer back through `gallery.cellAt`, i.e.
 *   through the lattice's own placement.
 * - A selection is a range of SORTED positions (shift-click, or ranks a–b); it
 *   writes every group in it to the marking, loaded or not (P7). Groups the
 *   marking holds are lit by the platform's `isLit`.
 * - Recovery is an EPOCH carried in every call's arguments. A failed answer
 *   (exit 3: no usable cache; exit 4: the cache was rebuilt since the index)
 *   moves the epoch on, and the args being the query key, the whole chain —
 *   build, index, pages — is asked again as new queries. Nothing waits on a
 *   cached answer's identity (a refetch with the same JSON keeps the same data
 *   object). Only a failure seen AT the current epoch moves it, so any number
 *   of pages failing together cost one rebuild, and a re-render costs none.
 *   After MAX_RECOVERIES the gallery stops and shows why.
 */
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { isLit, useMarking, useSandboxRun } from "@aiws/view-sdk";

import { cellAt, groupsLit, groupsPerPage, rangeMarking, sortedPositions, thumbnail, type FacetIndex } from "./gallery";
import type { RasterImage } from "./raster";
import { decodeColumn, type WireColumn } from "./wire";

const PLUGIN = "chart";
const UNUSABLE = 3;
const STALE = 4;
const TILE = 112; // px, a thumbnail and its label
const THUMB = 96;
const FALLBACK_VIEWPORT = { width: 800, height: 600 };
const MAX_RECOVERIES = 2;

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

function Canvas({
  image,
  size,
  onHover,
}: {
  image: RasterImage;
  size: number;
  onHover?: (at: { col: number; row: number } | null) => void;
}) {
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
          onHover({
            col: Math.floor(((e.clientX - r.left) / r.width) * image.width),
            row: Math.floor(((e.clientY - r.top) / r.height) * image.height),
          });
        })
      }
      onMouseLeave={onHover && (() => onHover(null))}
    />
  );
}

type Shared = {
  index: FacetIndex;
  cacheKey: string;
  epoch: number;
  scheme: "sequential" | "diverging";
  columns: number;
  lit: boolean[] | null;
  selected: ReadonlySet<number>;
  onPick: (position: number, shift: boolean) => void;
  onEnlarge: (position: number) => void;
};

/** Report a failed answer (exit 3 or 4) from the epoch it was asked in. */
type FailedAt = (epoch: number, why: string) => void;

/** One tile; its thumbnail is painted once per (column, lit), not per render. */
const Tile = memo(function Tile({
  index,
  position,
  rank,
  column,
  scheme,
  columns,
  lit,
  selected,
  onPick,
  onEnlarge,
}: {
  index: FacetIndex;
  position: number;
  rank: number;
  column: WireColumn | undefined;
  scheme: "sequential" | "diverging";
  columns: number;
  lit: boolean | undefined;
  selected: boolean;
  onPick: (position: number, shift: boolean) => void;
  onEnlarge: (position: number) => void;
}) {
  const image = useMemo(
    () => (column ? thumbnail(index, column, scheme, lit) : null),
    [index, column, scheme, lit],
  );
  const label = index.groups[position].key.join(" · ");
  return (
    <div
      style={{
        position: "absolute",
        left: (rank % columns) * TILE,
        top: Math.floor(rank / columns) * TILE,
        width: TILE - 8,
        outline: selected ? "2px solid var(--accent, #4a8)" : undefined,
      }}
    >
      <div
        role="button"
        tabIndex={0}
        aria-label={`group ${label}`}
        aria-pressed={selected}
        onClick={(e) => onPick(position, e.shiftKey)}
        onKeyDown={(e) => e.key === "Enter" && onPick(position, e.shiftKey)}
        style={{ cursor: "pointer" }}
      >
        {image ? <Canvas image={image} size={THUMB} /> : <div style={{ width: THUMB, height: THUMB }} />}
      </div>
      <div style={{ display: "flex", fontSize: 11, gap: 4 }}>
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <button type="button" aria-label="enlarge" onClick={() => onEnlarge(position)}>
          ⤢
        </button>
      </div>
    </div>
  );
});

function Page({
  positions,
  first,
  shared,
  failedAt,
}: {
  positions: number[];
  first: number;
  shared: Shared;
  failedAt: { current: FailedAt };
}) {
  const { index, cacheKey, epoch } = shared;
  const run = useSandboxRun(PLUGIN, "facet_page", { key: cacheKey, build: index.build, positions, epoch });
  const code = run.data?.exit_code;
  useEffect(() => {
    if (code === STALE || code === UNUSABLE) failedAt.current(epoch, run.data?.stderr ?? "");
  }, [code, epoch, failedAt, run.data]);
  const page = useMemo(() => parse<{ groups: WireColumn[] }>(run.data), [run.data]);
  return (
    <>
      {positions.map((p, k) => (
        <Tile
          key={p}
          index={index}
          position={p}
          rank={first + k}
          column={page?.groups[k]}
          scheme={shared.scheme}
          columns={shared.columns}
          lit={shared.lit ? shared.lit[p] : undefined}
          selected={shared.selected.has(p)}
          onPick={shared.onPick}
          onEnlarge={shared.onEnlarge}
        />
      ))}
    </>
  );
}

function Enlarged({
  shared,
  position,
  onClose,
  failedAt,
}: {
  shared: Shared;
  position: number;
  onClose: () => void;
  failedAt: { current: FailedAt };
}) {
  const { index, cacheKey, epoch } = shared;
  const page = useSandboxRun(PLUGIN, "facet_page", { key: cacheKey, build: index.build, positions: [position], epoch });
  const exact = useSandboxRun(PLUGIN, "facet_exact", { key: cacheKey, build: index.build, position, epoch });
  const codes = [page.data?.exit_code, exact.data?.exit_code];
  const failed = codes.some((c) => c === STALE || c === UNUSABLE);
  useEffect(() => {
    if (failed) failedAt.current(epoch, "");
  }, [failed, epoch, failedAt]);
  const column = useMemo(() => parse<{ groups: WireColumn[] }>(page.data)?.groups[0], [page.data]);
  const values = useMemo(() => {
    const wire = parse<WireColumn>(exact.data);
    if (!wire) return null;
    const col = decodeColumn(wire);
    return Array.from({ length: Math.min(index.cells, col.length) }, (_, i) => col.value(i));
  }, [exact.data, index.cells]);
  const [at, setAt] = useState<{ col: number; row: number } | null>(null);
  const image = useMemo(() => (column ? thumbnail(index, column, shared.scheme) : null), [index, column, shared.scheme]);
  const cell = at ? cellAt(index, at.col, at.row) : -1;
  const hovered = cell >= 0 && values ? values[cell] : null;
  const label = index.groups[position].key.join(" · ");
  return (
    <div
      role="dialog"
      aria-label={`group ${label}`}
      style={{ position: "absolute", inset: 0, background: "var(--bg, #fff)", padding: 12, zIndex: 1 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{label}</strong>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {image ? <Canvas image={image} size={384} onHover={setAt} /> : <Notice>Loading…</Notice>}
      <div style={{ fontSize: 12 }}>
        {hovered === null || hovered === undefined ? "Hover a cell for its exact value" : `value: ${String(hovered)}`}
      </div>
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

  const [epoch, setEpoch] = useState(0);
  const [gaveUp, setGaveUp] = useState<string | null>(null);
  const build = useSandboxRun(PLUGIN, "facet_build", { spec: text, epoch });
  // parsed once per answer: a fresh object every render would re-run every
  // effect and memo keyed on it
  const built = useMemo(() => parse<{ key: string; build: string; groups: number }>(build.data), [build.data]);
  const index = useSandboxRun(PLUGIN, "facet_index", { key: built?.key ?? "", epoch }, { enabled: !!built });
  const idx = useMemo(() => parse<FacetIndex>(index.data), [index.data]);

  // A failure at epoch `at` asks for epoch at + 1, and the epoch only ever moves
  // forward (max): any number of failures from one epoch move it once, and a
  // late one from an older epoch, already recovered from, moves nothing.
  const failedAt = useRef<FailedAt>(() => {});
  failedAt.current = (at, why) => {
    if (at >= MAX_RECOVERIES) {
      setGaveUp(why.trim() || "the gallery's cache could not be read");
      return;
    }
    setEpoch((e) => Math.max(e, at + 1));
  };
  const indexCode = index.data?.exit_code;
  useEffect(() => {
    if (indexCode === UNUSABLE) failedAt.current(epoch, index.data?.stderr || `exit ${indexCode}`);
  }, [indexCode, epoch, index.data]);

  const [order, setOrder] = useState(facet.sort?.order ?? "ascending");
  const sorted = useMemo(
    () => (idx ? sortedPositions(idx, facet.sort ? { field: facet.sort.field, order } : null) : []),
    [idx, facet.sort, order],
  );

  const [entry, write] = useMarking(marking);
  const lit = useMemo(() => (idx && entry ? groupsLit(idx, entry.marking, isLit) : null), [idx, entry]);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  // The outlines show THIS view's selection; once the marking holds something
  // else (another view wrote it, or it was cleared), they go.
  useEffect(() => {
    if (!entry || entry.source !== source) setSelected((s) => (s.size === 0 ? s : new Set()));
  }, [entry, source]);
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

  // Stable across renders that change neither: a Tile repaints only when its
  // own column, lit or selection changes.
  const sortedRef = useRef(sorted);
  sortedRef.current = sorted;
  const anchorRef = useRef(anchor);
  anchorRef.current = anchor;
  const writeRange = useRef((_: number[]) => {});
  writeRange.current = (positions: number[]) => {
    setSelected(new Set(positions));
    if (marking && idx) write(rangeMarking(idx, positions), source);
  };
  const onPick = useMemo(
    () => (p: number, shift: boolean) => {
      const order = sortedRef.current;
      const rank = order.indexOf(p);
      const a = anchorRef.current;
      if (shift && a !== null) {
        const from = order.indexOf(a);
        writeRange.current(order.slice(Math.min(from, rank), Math.max(from, rank) + 1));
      } else {
        setAnchor(p);
        writeRange.current([p]);
      }
    },
    [],
  );

  if (gaveUp) return <Notice role="alert">{`The gallery could not be opened: ${gaveUp}`}</Notice>;
  if (build.error) return <Notice role="alert">{build.error.message}</Notice>;
  if (build.data && build.data.exit_code !== 0)
    return <Notice role="alert">{build.data.stderr.trim() || `exit ${build.data.exit_code}`}</Notice>;
  if (!built) return <Notice>Building the gallery in the sandbox…</Notice>;
  if (index.data && index.data.exit_code !== 0 && index.data.exit_code !== UNUSABLE)
    return <Notice role="alert">{index.data.stderr.trim() || `exit ${index.data.exit_code}`}</Notice>;
  if (!idx) return <Notice>Opening the gallery…</Notice>;

  const shared: Shared = {
    index: idx,
    cacheKey: built.key,
    epoch,
    scheme,
    columns: Math.max(1, Math.floor(viewport.width / TILE)),
    lit,
    selected,
    onPick,
    onEnlarge: setEnlarged,
  };

  const perPage = groupsPerPage(idx.cells);
  const rows = Math.ceil(sorted.length / shared.columns);
  // a screen of lookahead each way: the next page is fetched before it shows
  const firstRank = Math.max(0, Math.floor((viewport.top - viewport.height) / TILE)) * shared.columns;
  const lastRank = Math.ceil((viewport.top + 2 * viewport.height) / TILE) * shared.columns;
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
            if (b >= a) writeRange.current(sorted.slice(a - 1, b));
          }}
        >
          Select ranks
        </button>
        <button type="button" onClick={() => writeRange.current([])}>
          Clear selection
        </button>
      </div>
      <div ref={scroller} data-gallery-scroll style={{ flex: 1, overflow: "auto", position: "relative" }}>
        <div style={{ position: "relative", height: rows * TILE }}>
          {pages.map((n) => (
            <Page
              key={n}
              positions={sorted.slice(n * perPage, (n + 1) * perPage)}
              first={n * perPage}
              shared={shared}
              failedAt={failedAt}
            />
          ))}
        </div>
      </div>
      {enlarged !== null && (
        <Enlarged shared={shared} position={enlarged} onClose={() => setEnlarged(null)} failedAt={failedAt} />
      )}
    </div>
  );
}
