/**
 * The usage page's third part (docs/plan-wui-usage.md U8): every deployed WUI
 * page — person-days opened, each kind of use and the time it took — for an
 * administrator. Drawn with the tool and skill parts' own pieces (the bars, the
 * table, the side panel, the raw-data link) so the three read alike (NN/g,
 * *Consistency and Standards*).
 *
 * It follows the page's window and App filter. Its uses are all made through a
 * page, so a surface other than "page" leaves nothing to show — said, not
 * hidden.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import type { WuiUsageClient, WuiUsagePage } from "../api/wuiUsage";
import { UserChip } from "../components/UserChip";
import { useT } from "../lib/i18n";
import { UsageBars } from "./ToolUsagePanel";

const cell: React.CSSProperties = { padding: "6px 8px", textAlign: "left" };
const num: React.CSSProperties = { ...cell, textAlign: "right", fontVariantNumeric: "tabular-nums" };

/** One page's key: an item and a view path. */
export const pageKey = (p: { item_id: string; page: string }) => `${p.item_id}\u0000${p.page}`;

function useDuration(): (ms: number) => string {
  const t = useT();
  return (ms) => {
    const s = ms / 1000;
    if (s < 60) return t("wui.usage.seconds", { n: Math.round(s) });
    if (s < 3600) return t("wui.usage.minutes", { n: Math.round(s / 60) });
    return t("wui.usage.hours", { n: (s / 3600).toFixed(1) });
  };
}

export function WuiUsagePart({
  days,
  shared,
  client,
  picked,
  pick,
  close,
}: {
  days: number;
  shared: { surface: string; app: string };
  client: WuiUsageClient;
  /** This part's page in the usage page's one side panel, if it is this part's. */
  picked: WuiUsagePage | null;
  pick: (row: WuiUsagePage | null, from?: HTMLElement) => void;
  close: () => void;
}) {
  const t = useT();
  const duration = useDuration();
  const headingId = useId();
  const report = useQuery({
    queryKey: qk.wuiUsage(days),
    queryFn: () => client.report(days),
    placeholderData: (prev) => prev,
  });
  const otherSurface = Boolean(shared.surface) && shared.surface !== "page";
  const rows = useMemo(() => {
    const all = report.data?.pages ?? [];
    return shared.app ? all.filter((r) => r.slug === shared.app) : all;
  }, [report.data, shared.app]);
  const name = (r: WuiUsagePage) => (r.title ? `${r.title} · ${r.page}` : r.page);
  const isPicked = (r: WuiUsagePage) => picked !== null && pageKey(picked) === pageKey(r);

  return (
    <section className="tool-usage-part" aria-labelledby={headingId}>
      <div className="tool-usage-part-head">
        <h2 id={headingId} className="tool-usage-part-title">
          {t("usage.part.wui")}
        </h2>
        <div className="tool-usage-part-tools">
          {/* A file to save, so a link — not a button. All of the window: the
              filters above are not applied, as for the other parts. */}
          <a className="tool-usage-raw" href={client.rawUrl(days)} download>
            {t("usage.raw")}
          </a>
        </div>
      </div>
      <p className="tool-usage-note">{t("usage.wui.what")}</p>

      {otherSurface ? (
        <p className="tool-usage-note">{t("usage.wui.notThisSurface")}</p>
      ) : report.isError ? (
        <p className="tool-usage-note" role="alert">
          {t("usage.wui.failed", { reason: (report.error as Error).message })}
        </p>
      ) : report.data && rows.length === 0 ? (
        <p className="tool-usage-note">{t("usage.wui.empty")}</p>
      ) : (
        <>
          <div className="tool-usage-charts">
            {/* Two units, so two scales: person-days and uses never share an axis. */}
            <UsageBars
              title={t("usage.wui.chart.personDays")}
              rows={rows.map((r) => ({ name: name(r), value: r.person_days }))}
              scale={Math.max(0, ...rows.map((r) => r.person_days))}
            />
            <UsageBars
              title={t("usage.wui.chart.uses")}
              rows={rows.map((r) => ({ name: name(r), value: r.uses }))}
              scale={Math.max(0, ...rows.map((r) => r.uses))}
            />
          </div>
          <div className="tool-usage-scroll">
            <table className="tool-usage-table">
              <thead>
                <tr>
                  <th style={cell}>{t("usage.wui.col.page")}</th>
                  <th style={cell}>{t("usage.col.item")}</th>
                  <th style={num}>{t("usage.wui.col.personDays")}</th>
                  <th style={num}>{t("usage.wui.col.workflow")}</th>
                  <th style={num}>{t("usage.wui.col.tool")}</th>
                  <th style={num}>{t("usage.wui.col.agent")}</th>
                  <th style={num}>{t("usage.wui.col.uses")}</th>
                  <th style={num}>{t("usage.wui.col.time")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={pageKey(r)} data-testid="wui-usage-row" aria-selected={isPicked(r)}>
                    <td style={cell}>
                      <button
                        type="button"
                        className="tool-usage-tool"
                        data-row-key={pageKey(r)}
                        onClick={(e) => pick(isPicked(r) ? null : r, e.currentTarget)}
                      >
                        {r.page}
                      </button>
                    </td>
                    <td style={cell}>
                      {r.slug && r.title ? (
                        <Link to={`/a/${encodeURIComponent(r.slug)}/${encodeURIComponent(r.item_id)}`}>{r.title}</Link>
                      ) : (
                        r.item_id
                      )}
                    </td>
                    <td style={num}>{r.person_days}</td>
                    <td style={num}>{r.workflow}</td>
                    <td style={num}>{r.tool}</td>
                    <td style={num}>{r.agent}</td>
                    <td style={num}>{r.uses}</td>
                    <td style={num}>{duration(r.uses_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {picked && rows.some(isPicked) && !otherSurface && (
        <WuiUsageDetailPanel row={picked} days={days} client={client} onClose={close} duration={duration} />
      )}
    </section>
  );
}

function WuiUsageDetailPanel({
  row,
  days,
  client,
  onClose,
  duration,
}: {
  row: WuiUsagePage;
  days: number;
  client: WuiUsageClient;
  onClose: () => void;
  duration: (ms: number) => string;
}) {
  const t = useT();
  const detail = useQuery({
    queryKey: qk.wuiUsageDetail(row.item_id, row.page, days),
    queryFn: () => client.detail(row.item_id, row.page, days),
  });
  const headingRef = useRef<HTMLHeadingElement>(null);
  // The panel takes the keyboard when it opens for a page (as the tool panel does).
  useEffect(() => {
    headingRef.current?.focus();
  }, [row.item_id, row.page]);
  return (
    <section
      role="region"
      className="tool-usage-sidepanel"
      data-testid="wui-usage-detail"
      aria-label={t("usage.detail.title", { name: row.page })}
      aria-busy={detail.isPending}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="tool-usage-sidepanel-head">
        <h3 ref={headingRef} tabIndex={-1} className="tool-usage-detail-title">
          {row.page}
        </h3>
        <button type="button" className="tool-usage-sidepanel-close" aria-label={t("usage.detail.close")} onClick={onClose}>
          ✕
        </button>
      </div>
      <p className="tool-usage-detail-what">{t("usage.wui.detail.what", { item: row.title || row.item_id, days })}</p>
      {detail.isPending && <p className="tool-usage-detail-what">{t("usage.detail.loading")}</p>}
      {detail.isError && <p role="alert">{t("usage.wui.failed", { reason: (detail.error as Error).message })}</p>}
      {detail.data && (
        <div className="tool-usage-scroll">
          <table className="tool-usage-table" aria-label={t("usage.by.user")}>
            <thead>
              <tr>
                <th style={cell}>{t("usage.col.user")}</th>
                <th style={num}>{t("usage.wui.col.personDays")}</th>
                <th style={num}>{t("usage.wui.col.opens")}</th>
                <th style={num}>{t("usage.wui.col.uses")}</th>
                <th style={num}>{t("usage.wui.col.time")}</th>
              </tr>
            </thead>
            <tbody>
              {detail.data.people.map((p) => (
                <tr key={p.user}>
                  <td style={cell}>
                    <UserChip userId={p.user} />
                  </td>
                  <td style={num}>{p.person_days}</td>
                  <td style={num}>{p.opens}</td>
                  <td style={num}>{p.uses}</td>
                  <td style={num}>{duration(p.uses_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
