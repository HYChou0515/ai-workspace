/**
 * The administrator's usage tab (docs/plan-tool-usage-stats.md §3.7, §8.4):
 * which tools the AI called, how often they failed, how long they took; and
 * which skills were loaded, from where — each drawn directly on one page,
 * then one tool or skill by person, by surface × App, by item.
 *
 * Guidance (cited in the plan):
 * - Tools and skills are both on the page, not behind a switch: the
 *   administrator compares and scans them, and what is not on screen is not
 *   compared (§8.4).
 * - Each kind's most used are a horizontal BAR chart — the chart for
 *   comparing categories, horizontal so long tool names show in full, and
 *   magnitude by length, never colour (NN/g, "Choosing Chart Types";
 *   "Dashboards: Making Charts and Graphs Easier to Understand"). Calls and
 *   failures are two charts side by side, not one stacked chart, which NN/g
 *   calls "very hard to understand".
 * - The exact numbers stay in a data table the administrator scans, sorts and
 *   acts on (NN/g, "Data Tables: Four Major User Tasks"): sortable columns,
 *   numbers right-aligned in one unit, and a row opens its breakdown.
 * - The window (7 / 30 / 90 days) is one choice among modes of the SAME
 *   content, so it is a segmented control (Material 3, "Segmented buttons").
 */
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  type ToolUsageClient,
  type ToolUsageTool,
  type UsageDays,
  type UsageGroup,
  type UsageKind,
  rowKey,
  toolUsageApi,
} from "../api/toolUsage";
import { qk } from "../api/queryKeys";
import { UserChip } from "../components/UserChip";
import { useApps } from "../hooks/useResources";
import { type MsgKey, useT } from "../lib/i18n";
import { UsageShownModal } from "./UsageShownModal";

const WINDOWS: UsageDays[] = [7, 30, 90];

const GROUPS: { group: UsageGroup; label: MsgKey }[] = [
  { group: "command", label: "usage.group.command" },
  { group: "package", label: "usage.group.package" },
];

/** How many a chart shows; the table below keeps every one. */
const CHART_TOP = 10;

const TOOL_SOURCES: Record<string, MsgKey> = {
  builtin: "usage.source.builtin",
  first_party: "usage.source.first_party",
  third_party: "usage.source.third_party",
};

const SKILL_SOURCES: Record<string, MsgKey> = {
  shared: "usage.source.shared",
  profile: "usage.source.profile",
  hub: "usage.source.hub",
  workspace: "usage.source.workspace",
};

const SURFACES: Record<string, MsgKey> = {
  chat: "usage.surface.chat",
  kb_chat: "usage.surface.kb_chat",
  workflow: "usage.surface.workflow",
  schedule: "usage.surface.schedule",
  subagent: "usage.surface.subagent",
  kb_agent: "usage.surface.kb_agent",
  background: "usage.surface.background",
};

type SortKey = "tool" | "source" | "calls" | "fails" | "rate" | "avg_ms";

function rate(t: { calls: number; fails: number }): number {
  return t.calls ? t.fails / t.calls : 0;
}

const cell: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid var(--paper-3)",
  textAlign: "left",
};
const num: React.CSSProperties = { ...cell, textAlign: "right", fontVariantNumeric: "tabular-nums" };

type Shared = { surface: string; app: string };

export function ToolUsagePanel({ client = toolUsageApi }: { client?: ToolUsageClient }) {
  const t = useT();
  const apps = useApps();
  const [days, setDays] = useState<UsageDays>(30);
  const [shared, setShared] = useState<Shared>({ surface: "", app: "" });
  const appTitle = (slug: string) => apps.find((a) => a.slug === slug)?.title ?? slug;
  // ONE side panel for the page (review r5): which row of which part it shows.
  // Each part keeping its own put two panels on top of each other.
  const [picked, setPicked] = useState<{ kind: UsageKind; row: ToolUsageTool } | null>(null);
  // The button a panel was opened from, to put the keyboard back on (review r5).
  const opener = useRef<HTMLElement | null>(null);
  const pick = (kind: UsageKind, row: ToolUsageTool | null, from?: HTMLElement) => {
    if (from) opener.current = from;
    setPicked(row ? { kind, row } : null);
  };
  const close = () => {
    setPicked(null);
    opener.current?.focus();
  };

  return (
    <div className="tool-usage" style={{ marginTop: 16 }}>
      <div className="tool-usage-controls">
        <div className="tool-usage-segmented" role="group" aria-label={t("usage.window")}>
          {WINDOWS.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={d === days}
              onClick={() => setDays(d)}
            >
              {t("usage.days", { n: d })}
            </button>
          ))}
        </div>
        <label>
          {t("usage.filter.surface")}
          <select
            className="input"
            value={shared.surface}
            onChange={(e) => setShared((f) => ({ ...f, surface: e.target.value }))}
          >
            <option value="">{t("usage.all")}</option>
            {Object.entries(SURFACES).map(([id, key]) => (
              <option key={id} value={id}>
                {t(key)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("usage.filter.app")}
          <select
            className="input"
            value={shared.app}
            onChange={(e) => setShared((f) => ({ ...f, app: e.target.value }))}
          >
            <option value="">{t("usage.all")}</option>
            {apps.map((a) => (
              <option key={a.slug} value={a.slug}>
                {a.title}
              </option>
            ))}
          </select>
        </label>
      </div>

      <UsagePart
        kind="tool"
        days={days}
        shared={shared}
        client={client}
        appTitle={appTitle}
        picked={picked?.kind === "tool" ? picked.row : null}
        pick={(row, from) => pick("tool", row, from)}
        close={close}
      />
      <UsagePart
        kind="skill"
        days={days}
        shared={shared}
        client={client}
        appTitle={appTitle}
        picked={picked?.kind === "skill" ? picked.row : null}
        pick={(row, from) => pick("skill", row, from)}
        close={close}
      />
    </div>
  );
}

/** One kind's part of the page: its charts, its table, its breakdown. */
function UsagePart({
  kind,
  days,
  shared,
  client,
  appTitle,
  picked,
  pick,
  close,
}: {
  kind: UsageKind;
  days: UsageDays;
  shared: Shared;
  client: ToolUsageClient;
  appTitle: (slug: string) => string;
  /** This part's row in the page's one side panel, if it is this part's. */
  picked: ToolUsageTool | null;
  pick: (row: ToolUsageTool | null, from?: HTMLElement) => void;
  close: () => void;
}) {
  const t = useT();
  const skills = kind === "skill";
  const sources = skills ? SKILL_SOURCES : TOOL_SOURCES;
  const [source, setSource] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "calls", desc: true });
  // Clearing touches only this part's own row: another part's open panel stays.
  const setPicked = (row: ToolUsageTool | null) => {
    if (row || picked) pick(row);
  };
  // §9: tools only — a skill has no package, and the ask was "which tools".
  const [group, setGroup] = useState<UsageGroup>("command");
  // What is UNticked, so a name the report newly brings shows (ticked) by default.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const filter = useMemo(() => ({ source, ...shared }), [source, shared]);
  const headingId = useId();

  const report = useQuery({
    queryKey: qk.toolUsage(days, filter, kind, group),
    queryFn: () => client.report(days, filter, kind, group),
  });
  const all = useMemo(() => report.data?.tools ?? [], [report.data]);
  // `hidden` holds row keys (`rowKey`), not names: one name can be two rows.
  const shown = useMemo(() => all.filter((r) => !hidden.has(rowKey(r))), [all, hidden]);

  // Commands and packages are two different sets of names: a new granularity
  // starts from every one shown, nothing open, sorted by use.
  const pickGroup = (next: UsageGroup) => {
    if (next === group) return;
    setGroup(next);
    setHidden(new Set());
    setPicked(null);
    setSort({ key: "calls", desc: true });
  };
  const toggle = (key: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const rows = useMemo(() => {
    const value = (r: ToolUsageTool): number | string =>
      sort.key === "rate" ? rate(r) : r[sort.key];
    return [...shown].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const c = typeof x === "string" ? x.localeCompare(String(y)) : x - (y as number);
      return (sort.desc ? -c : c) || a.tool.localeCompare(b.tool);
    });
  }, [shown, sort]);

  const ms = (n: number) =>
    n < 1000 ? t("usage.ms", { n }) : t("usage.seconds", { n: (n / 1000).toFixed(1) });
  const pct = (r: { calls: number; fails: number }) => `${Math.round(rate(r) * 100)}%`;

  const header = (key: SortKey, label: string, numeric = false) => {
    const on = sort.key === key;
    return (
      <th
        style={numeric ? num : cell}
        aria-sort={on ? (sort.desc ? "descending" : "ascending") : "none"}
      >
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="ghost"
          onClick={() => setSort(on ? { key, desc: !sort.desc } : { key, desc: true })}
        >
          {label}
          {on && <span aria-hidden="true">{sort.desc ? " ↓" : " ↑"}</span>}
        </button>
      </th>
    );
  };

  const failed = shown.filter((r) => r.fails > 0);
  // One scale for every chart of the part: failures side by side with calls
  // are only comparable on the same axis (a failure can never outnumber its calls).
  const scale = Math.max(0, ...shown.map((r) => r.calls));
  // A name that shows more than once (one skill, two sources) is told apart
  // on the chart by its source.
  const seen = new Map<string, number>();
  for (const r of shown) seen.set(r.tool, (seen.get(r.tool) ?? 0) + 1);
  // Two sources (a skill) are told apart by source; one source, two packages
  // (a command name two Apps share) by package.
  const label = (r: ToolUsageTool) =>
    (seen.get(r.tool) ?? 0) > 1
      ? `${r.tool}(${r.package || (sources[r.source] ? t(sources[r.source]) : r.source)})`
      : r.tool;
  const isPicked = (r: ToolUsageTool) => picked !== null && rowKey(picked) === rowKey(r);
  // The table, the tokens and a breakdown name a row against the whole
  // report (the chart against what is shown), so one row keeps one name.
  const twice = new Map<string, number>();
  for (const r of all) twice.set(r.tool, (twice.get(r.tool) ?? 0) + 1);
  const nameOf = (r: ToolUsageTool) =>
    (twice.get(r.tool) ?? 0) > 1
      ? `${r.tool}(${r.package || (sources[r.source] ? t(sources[r.source]) : r.source)})`
      : r.tool;
  // The tick boxes, busiest first — the order the chart and the table start in.
  const names = [...all].sort((a, b) => b.calls - a.calls || a.tool.localeCompare(b.tool));

  return (
    <section className="tool-usage-part" aria-labelledby={headingId}>
      <div className="tool-usage-part-head">
        <h2 id={headingId} className="tool-usage-part-title">
          {t(skills ? "usage.part.skill" : "usage.part.tool")}
        </h2>
        <div className="tool-usage-part-tools">
        <label>
          {t("usage.filter.source")}
          <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">{t("usage.all")}</option>
            {Object.entries(sources).map(([id, key]) => (
              <option key={id} value={id}>
                {t(key)}
              </option>
            ))}
          </select>
        </label>
        {/* §9.3: a file to save, so a link — not a button. The raw data is all of
            it for the window: the filters above are not applied. */}
        <a className="tool-usage-raw" href={client.rawUrl(days, kind)} download>
          {t("usage.raw")}
        </a>
        </div>
      </div>

      {!skills && (
        <div className="tool-usage-pick">
          <div className="tool-usage-segmented" role="group" aria-label={t("usage.group")}>
            {GROUPS.map((g) => (
              <button
                key={g.group}
                type="button"
                aria-pressed={g.group === group}
                onClick={() => pickGroup(g.group)}
              >
                {t(g.label)}
              </button>
            ))}
          </div>
          {names.length > 0 && (
            <ShownPicker
              names={names}
              hidden={hidden}
              toggle={toggle}
              setHidden={setHidden}
              nameOf={nameOf}
            />
          )}
        </div>
      )}

      {report.isError ? (
        <p className="tool-usage-note" role="alert">
          {t("usage.failed", { reason: (report.error as Error).message })}
        </p>
      ) : report.data && all.length === 0 ? (
        <p className="tool-usage-note">{t(skills ? "usage.empty.skill" : "usage.empty")}</p>
      ) : report.data && rows.length === 0 ? (
        <p className="tool-usage-note">{t("usage.shown.empty")}</p>
      ) : (
        <>
          <div className="tool-usage-charts">
            <UsageBars
              title={t(skills ? "usage.chart.loads" : "usage.chart.calls")}
              rows={shown.map((r) => ({ name: label(r), value: r.calls }))}
              scale={scale}
            />
            {/* Failures on a chart of their own — never stacked onto the calls. */}
            {!skills && failed.length > 0 && (
              <UsageBars
                title={t("usage.chart.fails")}
                rows={failed.map((r) => ({ name: label(r), value: r.fails }))}
                scale={scale}
              />
            )}
          </div>
          <div className="tool-usage-scroll">
            <table className="tool-usage-table">
              <thead>
                <tr>
                  {header("tool", t(skills ? "usage.col.skill" : "usage.col.tool"))}
                  {header("source", t("usage.col.source"))}
                  {header("calls", t(skills ? "usage.col.loads" : "usage.col.calls"), true)}
                  {/* A skill load has no failure and no time (D11). */}
                  {!skills && header("fails", t("usage.col.fails"), true)}
                  {!skills && header("rate", t("usage.col.rate"), true)}
                  {!skills && header("avg_ms", t("usage.col.avg"), true)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={rowKey(r)}
                    data-testid="tool-usage-row"
                    aria-selected={isPicked(r)}
                  >
                    <td style={cell}>
                      <button
                        type="button"
                        className="tool-usage-tool"
                        onClick={(e) => pick(isPicked(r) ? null : r, e.currentTarget)}
                      >
                        {nameOf(r)}
                      </button>
                    </td>
                    <td style={cell}>{sources[r.source] ? t(sources[r.source]) : r.source}</td>
                    <td style={num}>{r.calls}</td>
                    {!skills && (
                      <>
                        <td style={num}>{r.fails}</td>
                        <td style={num}>{pct(r)}</td>
                        <td style={num}>{ms(r.avg_ms)}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Only while its row is on the table: a breakdown of a row the filters
          or the picker took away would describe something no longer shown. */}
      {picked && rows.some(isPicked) && (
        <ToolDetail
          row={picked}
          title={nameOf(picked)}
          sourceLabel={sources[picked.source] ? t(sources[picked.source]) : picked.source}
          onClose={close}
          filtered={Boolean(shared.surface || shared.app)}
          kind={kind}
          group={group}
          days={days}
          client={client}
          ms={ms}
          pct={pct}
          appTitle={appTitle}
        />
      )}
    </section>
  );
}

/**
 * A value axis for counts: from 0 to the first round tick at or above `max`,
 * in about four steps of 1, 2 or 5 × 10ⁿ — never less than one, since what is
 * counted are whole calls.
 */
export function countAxis(max: number): { step: number; end: number } {
  const raw = Math.max(max, 1) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = Math.max(1, (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag);
  return { step, end: Math.max(step, Math.ceil(max / step) * step) };
}

/**
 * A horizontal bar chart of the largest `CHART_TOP`, largest first: magnitude
 * by bar length on a value axis from 0 to `scale` (shared across the part, so
 * charts side by side read on one scale), with a gridline at each tick and the
 * tick values beneath; the full name beside each bar and its number at the
 * end. The rows are a list, so a screen reader hears "name value" per bar; the
 * bars, gridlines and axis are decoration.
 */
function UsageBars({
  title,
  rows,
  scale,
}: {
  title: string;
  rows: { name: string; value: number }[];
  /** The largest value the axis must reach — shared by the charts it is compared with. */
  scale: number;
}) {
  const top = [...rows]
    .sort((a, b) => b.value - a.value || a.name.localeCompare(b.name))
    .slice(0, CHART_TOP);
  const { step, end } = countAxis(scale);
  const ticks = Array.from({ length: end / step + 1 }, (_, i) => i * step);
  const titleId = useId();
  return (
    <figure
      className="tool-usage-bars"
      aria-labelledby={titleId}
      style={{ "--usage-ticks": ticks.length - 1 } as React.CSSProperties}
    >
      <figcaption id={titleId} className="tool-usage-bars-title">
        {title}
      </figcaption>
      <div className="tool-usage-bars-plot">
        <ol>
          {top.map((r) => (
            <li key={r.name}>
              <span className="tool-usage-bars-name">{r.name}</span>{" "}
              <span className="tool-usage-bars-track" aria-hidden="true">
                <span data-bar style={{ width: `${Math.round((r.value / end) * 100)}%` }} />
              </span>{" "}
              <span className="tool-usage-bars-value">{r.value}</span>
            </li>
          ))}
        </ol>
        <div className="tool-usage-bars-axis" aria-hidden="true">
          <span />
          <span className="tool-usage-bars-scale">
            {ticks.map((v) => (
              <span key={v} data-tick style={{ left: `${(v / end) * 100}%` }}>
                {v}
              </span>
            ))}
          </span>
          <span />
        </div>
      </div>
    </figure>
  );
}

/** Past this many, the picked names are counted rather than listed. */
const CHIPS_MAX = 20;

/**
 * Which tools to show (§9.2), built for a hundred commands: one line saying
 * what is shown, which opens the tool modal's kind of picker
 * (`UsageShownModal`); what is picked stays in sight OUTSIDE it as removable
 * tokens (NN/g, "Listboxes vs. Dropdown Lists").
 */
function ShownPicker({
  names,
  hidden,
  toggle,
  setHidden,
  nameOf,
}: {
  names: ToolUsageTool[];
  hidden: ReadonlySet<string>;
  toggle: (name: string) => void;
  setHidden: (next: ReadonlySet<string>) => void;
  nameOf: (r: ToolUsageTool) => string;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const total = names.length;
  const picked = names.filter((r) => !hidden.has(rowKey(r)));
  const narrowed = picked.length < total;

  return (
    <div className="tool-usage-shown">
      <button
        type="button"
        className="tool-usage-shown-summary"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        {narrowed
          ? t("usage.shown.some", { k: picked.length, n: total })
          : t("usage.shown.every", { n: total })}
      </button>
      {narrowed && picked.length > 0 && picked.length <= CHIPS_MAX && (
        <ul className="tool-usage-chips" aria-label={t("usage.shown.chosen")}>
          {picked.map((r) => (
            <li key={rowKey(r)}>
              {nameOf(r)}
              <button
                type="button"
                aria-label={t("usage.shown.remove", { name: nameOf(r) })}
                onClick={() => toggle(rowKey(r))}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <UsageShownModal
          names={names}
          hidden={hidden}
          onApply={setHidden}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

/**
 * One row's details (§3.6, D4/D7) — in a NONMODAL SIDE PANEL beside the table:
 * NN/g, "Data Tables: Four Major User Tasks", on viewing one record — a modal
 * covers the rows a reader wants to compare against, an accordion clutters
 * the table, a side panel covers part of it and leaves the rest usable, so
 * picking another row swaps the panel.
 *
 * It says first what it is and the totals, in words (summary first), then
 * three plain-worded parts — who, where, which items — each count drawn as a
 * bar against the largest, as the page's charts are.
 */
/** An item id's first eight characters after its kind (`rca-item:aaaa1111-…`). */
function shortId(item: string): string {
  return item.slice(item.indexOf(":") + 1, item.indexOf(":") + 9);
}

function ToolDetail({
  row,
  title,
  sourceLabel,
  onClose,
  filtered,
  kind,
  group,
  days,
  client,
  ms,
  pct,
  appTitle,
}: {
  row: ToolUsageTool;
  /** How the table names this row (with its package or source when the name repeats). */
  title: string;
  sourceLabel: string;
  onClose: () => void;
  /** The page is filtered by surface or App, which a breakdown cannot be. */
  filtered: boolean;
  kind: UsageKind;
  group: UsageGroup;
  days: UsageDays;
  client: ToolUsageClient;
  ms: (n: number) => string;
  pct: (r: { calls: number; fails: number }) => string;
  appTitle: (slug: string) => string;
}) {
  const t = useT();
  const skills = kind === "skill";
  const pkg = row.package ?? "";
  const detail = useQuery({
    queryKey: qk.toolUsageDetail(row.tool, days, kind, group, `${row.source}\u0000${pkg}`),
    queryFn: () => client.detail(row.tool, days, kind, group, row.source, pkg),
  });
  const d = detail.data;
  const headingRef = useRef<HTMLHeadingElement>(null);
  // Opening a row (or another one) puts the reader at the panel's heading.
  useEffect(() => {
    headingRef.current?.focus();
  }, [row.tool, row.source, pkg]);

  // The totals from the breakdown itself (every call has a person, so the
  // "who" part adds up to the whole) — never from the table row, which the
  // page's filters narrowed and which a later window would leave stale.
  const total = d
    ? d.by_user.reduce(
        (acc, r) => ({
          calls: acc.calls + r.calls,
          fails: acc.fails + r.fails,
          ms: acc.ms + r.avg_ms * r.calls,
        }),
        { calls: 0, fails: 0, ms: 0 },
      )
    : null;
  const summary = !total
    ? null
    : skills
      ? t("usage.detail.loads", { n: total.calls })
      : t("usage.detail.calls", {
          n: total.calls,
          f: total.fails,
          rate: pct(total),
          avg: ms(total.calls ? Math.round(total.ms / total.calls) : 0),
        });

  const part = (
    heading: string,
    /** What the first column holds — not the heading again. */
    column: string,
    rows: { key: string; label: ReactNode; calls: number; fails: number; avg_ms: number }[],
  ) => {
    const max = Math.max(1, ...rows.map((r) => r.calls));
    return (
      <>
        <h4 className="tool-usage-detail-part">{heading}</h4>
        <div className="tool-usage-scroll">
          <table className="tool-usage-table" aria-label={heading}>
            <thead>
              <tr>
                <th style={cell}>{column}</th>
                <th style={num}>{t(skills ? "usage.col.loads" : "usage.col.calls")}</th>
                {!skills && (
                  <>
                    <th style={num}>{t("usage.col.fails")}</th>
                    <th style={num}>{t("usage.col.rate")}</th>
                    <th style={num}>{t("usage.col.avg")}</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td style={cell}>{r.label}</td>
                  <td style={num}>
                    <span className="tool-usage-cellbar">
                      <span className="tool-usage-cellbar-track" aria-hidden="true">
                        <span data-bar style={{ width: `${Math.round((r.calls / max) * 100)}%` }} />
                      </span>
                      {r.calls}
                    </span>
                  </td>
                  {!skills && (
                    <>
                      <td style={num}>{r.fails}</td>
                      <td style={num}>{pct(r)}</td>
                      <td style={num}>{ms(r.avg_ms)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  };

  return (
    <section
      role="region"
      className="tool-usage-sidepanel"
      data-testid="tool-usage-detail"
      aria-label={t("usage.detail.title", { name: title })}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="tool-usage-sidepanel-head">
        <h3 ref={headingRef} tabIndex={-1} className="tool-usage-detail-title">
          {title}
        </h3>
        <button
          type="button"
          className="tool-usage-sidepanel-close"
          aria-label={t("usage.detail.close")}
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      <p className="tool-usage-detail-what">
        {t("usage.detail.what", {
          kind: t(skills ? "usage.part.skill" : "usage.part.tool"),
          source: sourceLabel,
          days,
        })}
      </p>
      {summary && (
        <p className="tool-usage-detail-summary" data-testid="tool-usage-summary">
          {summary}
        </p>
      )}
      {/* A breakdown is three separate sums (who, where, which item), not a
          cross-table: it cannot be narrowed by surface or App. Said, not hidden. */}
      {filtered && <p className="tool-usage-detail-what">{t("usage.detail.unfiltered")}</p>}
      {detail.isError && (
        <p role="alert">{t("usage.failed", { reason: (detail.error as Error).message })}</p>
      )}
      {d && (
        <>
          {part(
            t("usage.by.user"),
            t("usage.col.user"),
            d.by_user.map((r) => ({ ...r, key: r.user, label: <UserChip userId={r.user} /> })),
          )}
          {part(
            t("usage.by.surface"),
            t("usage.col.surface"),
            d.by_surface_app.map((r) => ({
              ...r,
              key: `${r.surface}\u0000${r.app}`,
              label: `${t(SURFACES[r.surface] ?? "usage.surface.background")}${r.app ? ` · ${appTitle(r.app)}` : ""}`,
            })),
          )}
          {part(
            t("usage.by.item"),
            t("usage.col.item"),
            d.by_item.map((r) => ({
              ...r,
              key: r.item,
              label:
                r.slug && r.title ? (
                  <>
                    <Link to={`/a/${encodeURIComponent(r.slug)}/${encodeURIComponent(r.item)}`}>
                      {r.title}
                    </Link>
                    {/* Two items can share a title: then each says which App
                        and which item it is. */}
                    {d.by_item.filter((o) => o.title === r.title).length > 1 && (
                      <span className="tool-usage-detail-which">
                        {`${appTitle(r.slug)} · ${shortId(r.item)}`}
                      </span>
                    )}
                  </>
                ) : (
                  r.item || t("usage.noItem")
                ),
            })),
          )}
        </>
      )}
    </section>
  );
}
