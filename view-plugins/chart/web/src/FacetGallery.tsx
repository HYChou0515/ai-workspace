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

import { cellAt, groupLabel, groupsLit, groupsPerPage, rangeMarking, sortedPositions, thumbnail, type FacetIndex } from "./gallery";
import type { RasterImage } from "./raster";
import { viewCall } from "./viewCall";
import { decodeColumn, type WireColumn } from "./wire";

const PLUGIN = "chart";
const UNUSABLE = 3;
const STALE = 4;
const THUMB = 96;
const LABEL = 18; // px, the label row under a thumbnail: its name and ⤢
// the pitch between tiles: across, a thumbnail and a gap; down, the thumbnail,
// its label row and a gap -- a row shorter than that put the label (and its
// ⤢) under the next row's thumbnails
const TILE_W = THUMB + 16;
const TILE_H = THUMB + LABEL + 8;
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

export function Canvas({
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
      style={{ display: "block", width: size, height: size, imageRendering: "pixelated", background: "var(--bg-sunken, #0000)" }}
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
  const label = groupLabel(index, position);
  return (
    <div
      style={{
        position: "absolute",
        left: (rank % columns) * TILE_W,
        top: Math.floor(rank / columns) * TILE_H,
        width: THUMB,
        height: THUMB + LABEL,
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
        {image ? <Canvas image={image} size={THUMB} /> : <div style={{ display: "block", width: THUMB, height: THUMB }} />}
      </div>
      <div style={{ display: "flex", alignItems: "center", height: LABEL, overflow: "hidden", fontSize: 11, gap: 4 }}>
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <button
          type="button"
          aria-label="enlarge"
          onClick={() => onEnlarge(position)}
          style={{ padding: 0, border: 0, background: "none", font: "inherit", lineHeight: 1, cursor: "pointer" }}
        >
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
  if (run.error)
    return (
      <div role="alert" style={{ position: "absolute", left: 0, top: Math.floor(first / shared.columns) * TILE_H, padding: 8, color: "var(--err)" }}>
        {run.error.message}
      </div>
    );
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
  const failure = [page.data, exact.data].find((d) => d?.exit_code === STALE || d?.exit_code === UNUSABLE);
  useEffect(() => {
    if (failure) failedAt.current(epoch, failure.stderr.trim() || `exit ${failure.exit_code}`);
  }, [failure, epoch, failedAt]);
  const httpError = page.error ?? exact.error;
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
  const label = groupLabel(index, position);
  // it covers the gallery, so it takes focus: Escape is then heard here, as
  // any dialog is expected to close on it; closing gives focus back to what
  // opened it (the ⤢) rather than dropping it to the page
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement;
    box.current?.focus({ preventScroll: true });
    return () => {
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, []);
  return (
    <div
      ref={box}
      role="dialog"
      aria-label={`group ${label}`}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        // the host's modals hear Escape on document: this press is ours alone
        e.stopPropagation();
        onClose();
      }}
      style={{ position: "absolute", inset: 0, background: "var(--bg, #fff)", padding: 12, zIndex: 1 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{label}</strong>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {httpError ? (
        <Notice role="alert">{httpError.message}</Notice>
      ) : image ? (
        <Canvas image={image} size={384} onHover={setAt} />
      ) : (
        <Notice>Loading…</Notice>
      )}
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
  /** The spec's text: `facet_build` is keyed on its digest (`viewCall`), and
   * takes the text itself only for a view with no file. */
  text: string;
  marking: string | null;
  source: string | null;
}) {
  const facet = doc.facet as { sort?: { field: string; order?: "ascending" | "descending" } };
  const encoding = doc.encoding as { color?: { scale?: { scheme?: "sequential" | "diverging" } } };
  const scheme = encoding.color?.scale?.scheme ?? "sequential";

  const [epoch, setEpoch] = useState(0);
  const [gaveUp, setGaveUp] = useState<string | null>(null);
  // the view file, not its text (#847/#848 P9): see viewCall
  const call = useMemo(() => viewCall(text, source), [text, source]);
  const build = useSandboxRun(PLUGIN, "facet_build", { ...call, epoch });
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
  // the build itself can find its fresh cache gone before it reads it back
  const buildCode = build.data?.exit_code;
  useEffect(() => {
    if (buildCode === UNUSABLE) failedAt.current(epoch, build.data?.stderr || `exit ${buildCode}`);
  }, [buildCode, epoch, build.data]);

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
  // where the person had scrolled: a recovery reloads build and index, which
  // unmounts the scroller; the new one opens where the old one was
  const savedTop = useRef(0);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (el.scrollTop === 0 && savedTop.current > 0) el.scrollTop = savedTop.current;
    const measure = () => {
      savedTop.current = el.scrollTop;
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
  if (build.data && build.data.exit_code !== 0 && build.data.exit_code !== UNUSABLE)
    return <Notice role="alert">{build.data.stderr.trim() || `exit ${build.data.exit_code}`}</Notice>;
  if (!built) return <Notice>Building the gallery in the sandbox…</Notice>;
  if (index.error) return <Notice role="alert">{index.error.message}</Notice>;
  if (index.data && index.data.exit_code !== 0 && index.data.exit_code !== UNUSABLE)
    return <Notice role="alert">{index.data.stderr.trim() || `exit ${index.data.exit_code}`}</Notice>;
  if (!idx) return <Notice>Opening the gallery…</Notice>;

  const shared: Shared = {
    index: idx,
    cacheKey: built.key,
    epoch,
    scheme,
    columns: Math.max(1, Math.floor(viewport.width / TILE_W)),
    lit,
    selected,
    onPick,
    onEnlarge: setEnlarged,
  };

  const perPage = groupsPerPage(idx.cells);
  const rows = Math.ceil(sorted.length / shared.columns);
  // a screen of lookahead each way: the next page is fetched before it shows
  const firstRank = Math.max(0, Math.floor((viewport.top - viewport.height) / TILE_H)) * shared.columns;
  const lastRank = Math.ceil((viewport.top + 2 * viewport.height) / TILE_H) * shared.columns;
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
      {/* bounded by the window, not the pane: a host pane is height:auto, and a
          scroller that grows to its content mounts every tile and asks every page */}
      <div
        ref={scroller}
        data-gallery-scroll
        style={{ flex: 1, overflow: "auto", position: "relative", maxHeight: "80vh" }}
      >
        <div style={{ position: "relative", height: rows * TILE_H }}>
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
