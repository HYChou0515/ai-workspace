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

import {
  cellAt,
  cellsLit,
  groupLabel,
  groupsLit,
  groupsPerPage,
  rangeMarking,
  sortArgs,
  sortedPositions,
  stackImage,
  stackSet,
  thumbnail,
  tilesInBox,
  type FacetIndex,
  type SortChoice,
} from "./gallery";
import { categoryColour, type RasterImage } from "./raster";
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
const PROGRESS_POLL_MS = 1000;
const ENLARGED_PX = 384; // an enlarged map's size, where the view has room
const ENLARGED_PAD = 12;

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

/** A category gallery's levels, each with the colour its cells are painted
 * (P19); nothing for a continuous one. */
function Legend({ scale }: { scale: FacetIndex["scale"] }) {
  if (scale.kind !== "category") return null;
  return (
    <ul
      aria-label="legend"
      style={{ display: "flex", flexWrap: "wrap", gap: 10, listStyle: "none", margin: 0, padding: "2px 12px 6px", fontSize: 11 }}
    >
      {scale.labels.map((label, i) => (
        <li key={label} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <span aria-hidden style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: categoryColour(i) }} />
          {label}
        </li>
      ))}
    </ul>
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
      data-tile
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
        <span title={label} style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
        </span>
        <button
          type="button"
          className="btn"
          data-variant="ghost"
          data-size="sm"
          aria-label="enlarge"
          onClick={() => onEnlarge(position)}
          // the house ghost button, at the label row's height: a 28px one
          // would push the label under the next row of thumbnails
          style={{ height: LABEL, padding: "0 3px", fontSize: 11, lineHeight: 1 }}
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
  // any other exit (2: the call was refused) is not recovered by a rebuild:
  // say why, rather than sit at "Hover a cell…" for good
  const refused = [page.data, exact.data].find((d) => d && d.exit_code !== 0 && d.exit_code !== STALE && d.exit_code !== UNUSABLE);
  const httpError =
    page.error ?? exact.error ?? (refused ? new Error(refused.stderr.trim() || `exit ${refused.exit_code}`) : null);
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
  // P21: as wide as the view allows, up to ENLARGED_PX -- a fixed 384 px ran
  // off the right at 390 wide, and the cells there could not be hovered. The
  // hover math reads the canvas's drawn box, so it follows the scale.
  const [fit, setFit] = useState(ENLARGED_PX);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const room = el.clientWidth - 2 * ENLARGED_PAD;
      setFit(room > 0 ? Math.min(ENLARGED_PX, room) : ENLARGED_PX);
    };
    measure();
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    resize?.observe(el);
    return () => resize?.disconnect();
  }, []);
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
      style={{ position: "absolute", inset: 0, background: "var(--bg, #fff)", padding: ENLARGED_PAD, zIndex: 1 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{label}</strong>
        <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={onClose}>
          Close
        </button>
      </div>
      {httpError ? (
        <Notice role="alert">{httpError.message}</Notice>
      ) : image ? (
        <Canvas image={image} size={fit} onHover={setAt} />
      ) : (
        <Notice>Loading…</Notice>
      )}
      <Legend scale={index.scale} />
      <div style={{ fontSize: 12 }}>
        {hovered === null || hovered === undefined ? "Hover a cell for its exact value" : `value: ${String(hovered)}`}
      </div>
    </div>
  );
}

const STACK_PX = 240; // one map alone
const STACK_SMALL_PX = 150; // each of A, B and A - B

/** A number short enough for a caption. */
function short(v: number): string {
  return String(Number(v.toPrecision(4)));
}

function StackMap({
  label,
  wire,
  index,
  scheme,
  lit,
  size,
}: {
  label: string;
  wire: WireColumn | null;
  index: FacetIndex;
  scheme: "sequential" | "diverging";
  lit: boolean[] | null;
  size: number;
}) {
  const painted = useMemo(() => (wire ? stackImage(index, wire, scheme, lit) : null), [index, wire, scheme, lit]);
  return (
    <figure aria-label={label} style={{ margin: 0 }}>
      <figcaption style={{ fontSize: 11, marginBottom: 2 }}>
        {painted ? `${label} · ${short(painted.min)} – ${short(painted.max)}` : `${label} · no values`}
      </figcaption>
      {painted ? <Canvas image={painted.image} size={size} /> : <div style={{ width: size, height: size }} />}
    </figure>
  );
}

/** The stack panel's picks; null column / stat: the default. */
type StackChoice = { column: string | null; stat: string | null; b: string[][] | null };

type StackAnswer = { a: WireColumn; b: WireColumn | null; diff: WireColumn | null; groups: { a: number; b: number | null } };

/**
 * P5: one map stacking the chosen tiles -- the selection, else the tiles the
 * marking lights, else every tile -- at each cell the picked column by the
 * picked statistic, computed in the sandbox over the gallery's cache
 * (`facet_stack`). "Set as B" keeps the current tiles as B; the panel then
 * shows A, B and A - B. Its cells light by the marking's x / y, as a grid's do.
 */
function StackPanel({
  shared,
  failedAt,
  a,
  colorField,
  cellLit,
  choice,
  onChoice,
}: {
  shared: Shared;
  failedAt: { current: FailedAt };
  a: string[][] | null;
  colorField: string | undefined;
  cellLit: boolean[] | null;
  /** Held by the gallery, not here: a re-sort rebuilds, and the panel goes
   * while it runs; what the person picked, B above all, must outlive that. */
  choice: StackChoice;
  onChoice: (next: StackChoice) => void;
}) {
  const { index, cacheKey, epoch, scheme } = shared;
  const columns = index.columns;
  // the colour column until one is picked
  const column = choice.column ?? (columns.find((c) => c.name === colorField) ?? columns[0])?.name ?? "";
  const picked = columns.find((c) => c.name === column);
  const stat = choice.stat ?? picked?.stack[0] ?? "count";
  const b = choice.b;
  const setColumn = (next: string) => onChoice({ ...choice, column: next, stat: null });
  const setStat = (next: string) => onChoice({ ...choice, column, stat: next });
  const setB = (next: string[][] | null) => onChoice({ ...choice, b: next });
  const run = useSandboxRun(PLUGIN, "facet_stack", { key: cacheKey, build: index.build, a, b, column, stat, epoch });
  const code = run.data?.exit_code;
  useEffect(() => {
    if (code === STALE || code === UNUSABLE) failedAt.current(epoch, run.data?.stderr || `exit ${code}`);
  }, [code, epoch, failedAt, run.data]);
  const answer = useMemo(() => parse<StackAnswer>(run.data), [run.data]);
  const refused =
    run.error?.message ??
    (run.data && code !== 0 && code !== STALE && code !== UNUSABLE ? run.data.stderr.trim() || `exit ${code}` : null);
  const tiles = (n: number) => `${n} tile${n === 1 ? "" : "s"}`;
  const size = b ? STACK_SMALL_PX : STACK_PX;
  return (
    <section
      aria-label="stack"
      style={{ flex: "0 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 6, padding: "4px 12px", fontSize: 12 }}
    >
      <strong>Stack</strong>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
        <select
          className="input"
          aria-label="stack column"
          value={column}
          onChange={(e) => setColumn(e.target.value)}
        >
          {columns.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
        <select className="input" aria-label="stack statistic" value={stat} onChange={(e) => setStat(e.target.value)}>
          {(picked?.stack ?? []).map((s) => (
            <option key={s} value={s}>
              {s === "distinct" ? "distinct count" : s}
            </option>
          ))}
        </select>
      </div>
      <div>{a ? `A: ${tiles(a.length)}` : `A: all ${tiles(index.groups.length)}`}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          disabled={!a}
          onClick={() => setB(a)}
          title="Keep the tiles A stacks now as B"
        >
          Set as B
        </button>
        {b && (
          <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setB(null)}>
            {`Clear B (${tiles(b.length)})`}
          </button>
        )}
      </div>
      {refused ? (
        <Notice role="alert">{refused}</Notice>
      ) : !answer ? (
        <Notice>Stacking…</Notice>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <StackMap label="A" wire={answer.a} index={index} scheme={scheme} lit={cellLit} size={size} />
          {answer.b && <StackMap label="B" wire={answer.b} index={index} scheme={scheme} lit={cellLit} size={size} />}
          {answer.diff && (
            <StackMap label="A − B" wire={answer.diff} index={index} scheme="diverging" lit={cellLit} size={size} />
          )}
        </div>
      )}
    </section>
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
  const facet = doc.facet as { sort?: { field: string; stat?: string; order?: "ascending" | "descending" } };
  const encoding = doc.encoding as {
    x?: { field?: string };
    y?: { field?: string };
    color?: { field?: string; scale?: { scheme?: "sequential" | "diverging" } };
  };
  const scheme = encoding.color?.scale?.scheme ?? "sequential";

  const [epoch, setEpoch] = useState(0);
  const [gaveUp, setGaveUp] = useState<string | null>(null);
  // the view file, not its text (#847/#848 P9): see viewCall
  const call = useMemo(() => viewCall(text, source), [text, source]);
  // P4: what the gallery sorts by -- the spec's own sort until the menu picks
  // another. A column (and statistic) is part of the cache, so a new choice is
  // a new build; the order is not, and flips over the index in hand.
  const [choice, setChoice] = useState<SortChoice>(facet.sort ? { field: facet.sort.field, stat: facet.sort.stat } : null);
  const buildArgs = { ...call, ...sortArgs(doc, choice) };
  const build = useSandboxRun(PLUGIN, "facet_build", { ...buildArgs, epoch });
  // P10: the runner answers a command only when it ends, so while the build
  // runs the gallery asks it how far it is (`facet_progress`, a quick command
  // that reads the lines the build writes as it goes, named by the same
  // arguments), once a second.
  const building = !build.data && !build.error;
  const progress = useSandboxRun(PLUGIN, "facet_progress", buildArgs, { enabled: building });
  const pollProgress = useRef(progress.refetch);
  pollProgress.current = progress.refetch;
  useEffect(() => {
    if (!building) return;
    const timer = setInterval(() => pollProgress.current(), PROGRESS_POLL_MS);
    return () => clearInterval(timer);
  }, [building]);
  const progressLines = useMemo(() => parse<{ lines: string[] }>(progress.data)?.lines ?? [], [progress.data]);
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
    () => (idx ? sortedPositions(idx, choice ? { field: choice.field, order } : null) : []),
    [idx, choice, order],
  );

  const [entry, write] = useMarking(marking);
  const lit = useMemo(() => (idx && entry ? groupsLit(idx, entry.marking, isLit) : null), [idx, entry]);
  const cellLit = useMemo(
    () => (idx && entry ? cellsLit(idx, encoding.x?.field ?? "", encoding.y?.field ?? "", entry.marking, isLit) : null),
    [idx, entry, encoding.x?.field, encoding.y?.field],
  );
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
  const [stackChoice, setStackChoice] = useState<StackChoice>({ column: null, stat: null, b: null });

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

  // P3 box select, as a file manager: a drag from empty space replaces the
  // selection with every tile the box touches; Shift-drag adds them. The hit
  // test is the layout arithmetic (tilesInBox), so tiles the wall has not
  // drawn are hit too.
  const [band, setBand] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const columnsRef = useRef(1);
  const endDrag = useRef<(() => void) | null>(null);
  useEffect(() => () => endDrag.current?.(), []);
  const startBox = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as Element).closest("[data-tile]")) return;
    const wall = e.currentTarget;
    const at = (ev: { clientX: number; clientY: number }) => {
      const r = wall.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };
    const { x: x0, y: y0 } = at(e);
    const add = e.shiftKey;
    e.preventDefault(); // no text selection under the band
    setBand({ x0, y0, x1: x0, y1: y0 });
    const move = (ev: MouseEvent) => {
      const { x, y } = at(ev);
      setBand({ x0, y0, x1: x, y1: y });
    };
    const up = (ev: MouseEvent) => {
      endDrag.current?.();
      const { x, y } = at(ev);
      const ranks = tilesInBox(
        { x0, y0, x1: x, y1: y },
        { count: sortedRef.current.length, columns: columnsRef.current, pitchX: TILE_W, pitchY: TILE_H, width: THUMB, height: THUMB + LABEL },
      );
      const hits = ranks.map((r) => sortedRef.current[r]);
      writeRange.current(add ? [...new Set([...selectedRef.current, ...hits])] : hits);
    };
    endDrag.current = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      endDrag.current = null;
      setBand(null);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  if (gaveUp) return <Notice role="alert">{`The gallery could not be opened: ${gaveUp}`}</Notice>;
  if (build.error) return <Notice role="alert">{build.error.message}</Notice>;
  if (build.data && build.data.exit_code !== 0 && build.data.exit_code !== UNUSABLE)
    return <Notice role="alert">{build.data.stderr.trim() || `exit ${build.data.exit_code}`}</Notice>;
  if (!built)
    return (
      <Notice>
        {"Building the gallery in the sandbox…"}
        {progressLines.length > 0 && (
          <span style={{ display: "block", marginTop: 6, fontFamily: "var(--font-mono, monospace)", fontSize: 12 }}>
            {progressLines.join("\n")}
          </span>
        )}
      </Notice>
    );
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
  columnsRef.current = shared.columns;

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
  const sortColumn = choice ? idx.columns.find((c) => c.name === choice.field) : undefined;
  const stackA = stackSet(idx, selected, lit);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", position: "relative" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "4px 12px", fontSize: 12 }}>
        <span>{`${sorted.length} groups`}</span>
        {lit && <span>{`${marked} of ${sorted.length} marked`}</span>}
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          sort by
          <select
            className="input"
            aria-label="sort by"
            value={choice?.field ?? ""}
            onChange={(e) => {
              const picked = idx.columns.find((c) => c.name === e.target.value);
              setChoice(picked ? (picked.single ? { field: picked.name } : { field: picked.name, stat: picked.stats[0] }) : null);
            }}
          >
            <option value="">written order</option>
            {idx.columns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {sortColumn && choice && (!sortColumn.single || choice.stat) && (
          <select
            className="input"
            aria-label="sort statistic"
            // its own width: `.input` fills a flex row, and this one is the toolbar
            style={{ flex: "none" }}
            value={choice.stat ?? ""}
            onChange={(e) => setChoice({ field: choice.field, stat: e.target.value })}
          >
            {sortColumn.stats.map((s) => (
              <option key={s} value={s}>
                {s === "distinct" ? "distinct count" : s}
              </option>
            ))}
          </select>
        )}
        {choice && (
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            onClick={() => setOrder(order === "ascending" ? "descending" : "ascending")}
          >
            {order}
          </button>
        )}
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          from rank
          <input className="input" value={from} onChange={(e) => setFrom(e.target.value)} style={{ flex: "none", width: 64 }} />
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          to rank
          <input className="input" value={to} onChange={(e) => setTo(e.target.value)} style={{ flex: "none", width: 64 }} />
        </label>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          onClick={() => {
            const a = Math.max(1, Number.parseInt(from, 10) || 1);
            const b = Math.min(sorted.length, Number.parseInt(to, 10) || a);
            if (b >= a) writeRange.current(sorted.slice(a - 1, b));
          }}
        >
          Select ranks
        </button>
        <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => writeRange.current([])}>
          Clear selection
        </button>
      </div>
      {/* bounded by the window, not the pane: a host pane is height:auto, and a
          scroller that grows to its content mounts every tile and asks every page */}
      <Legend scale={idx.scale} />
      {/* the wall and the stack panel side by side; on a narrow screen the
          panel wraps under the wall */}
      <div style={{ display: "flex", flexWrap: "wrap", flex: 1, minHeight: 0, gap: 8 }}>
        <div
          ref={scroller}
          data-gallery-scroll
          style={{ flex: "1 1 360px", minWidth: 0, overflow: "auto", position: "relative", maxHeight: "80vh" }}
        >
          <div data-gallery-wall onMouseDown={startBox} style={{ position: "relative", height: rows * TILE_H }}>
            {band && (
              <div
                data-gallery-band
                aria-hidden
                style={{
                  position: "absolute",
                  left: Math.min(band.x0, band.x1),
                  top: Math.min(band.y0, band.y1),
                  width: Math.abs(band.x1 - band.x0),
                  height: Math.abs(band.y1 - band.y0),
                  border: "1px dashed var(--accent, #4a8)",
                  background: "color-mix(in srgb, var(--accent, #4a8) 12%, transparent)",
                  pointerEvents: "none",
                  zIndex: 1,
                }}
              />
            )}
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
        <StackPanel
          shared={shared}
          failedAt={failedAt}
          a={stackA}
          colorField={encoding.color?.field}
          cellLit={cellLit}
          choice={stackChoice}
          onChoice={setStackChoice}
        />
      </div>
      {enlarged !== null && (
        <Enlarged shared={shared} position={enlarged} onClose={() => setEnlarged(null)} failedAt={failedAt} />
      )}
    </div>
  );
}
