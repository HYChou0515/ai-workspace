/**
 * `/wui` — every Deployed WUI the viewer may open, in one place
 * (`docs/plan-wui-overview.md`).
 *
 * The rows are the server's and so is the filter: `GET /wui` returns exactly
 * the pages this viewer could open by address, and says per row whether they
 * may Remove it. This page draws them, grouped by App the way My resources
 * groups its rows, newest Deploy first within a group — the server's order,
 * kept.
 *
 * A row leaves on purpose only (Remove) or because its item is gone or closed
 * to the viewer (the server drops it). Never because the view file is missing:
 * a sandbox mid-restore says "not found" too, and unlisting on that would make
 * pages blink out after every idle reap. A dead row opens the reader's own
 * "not published yet — or still being restored" sentence, and somebody presses
 * Remove.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import { exactTime, relativeTime } from "../api/types";
import { type DeployedWui, type WuiApi, wuiAddress, wuiApi } from "../api/wui";
import { AppTag } from "../components/AppTag";
import { useDialog } from "../components/Dialog";
import { Icon } from "../components/Icon";
import { PageMark } from "../components/PageMark";
import { UserChip } from "../components/UserChip";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { useUser } from "../hooks/useUsers";
import { useT } from "../lib/i18n";
import { useApps } from "../hooks/useResources";
import { appTagPalette, pageColour } from "../lib/appColor";
import { pxToRem } from "../lib/pxToRem";
import { favouriteKey, useWuiFavourites } from "../lib/wuiFavourites";
import { type WuiView, useWuiView } from "../lib/wuiView";

export function WuiOverviewPage({ client = wuiApi }: { client?: WuiApi }) {
  const t = useT();
  // The bar's trail is "latest caller wins": without this, arriving from an
  // item's workspace left the bar naming that item over this page. Same shape
  // as Help / Diagnostics / Review; "WUI" is the proper noun the menu uses.
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: "WUI" }]);
  // The viewer's stars — ONE set for the page, so the two copies of a starred
  // row (its App group and the favourites group) read and flip together. Per
  // signed-in user: the id is the "default-user" placeholder until the user
  // query settles, and the hook re-reads when it changes — so until `ready`
  // the star is held (disabled). A cold deep-link races the listing against
  // that query, and a press in the window was filed under the placeholder and
  // silently unfilled when the real id arrived (code review of P14).
  const me = useCurrentUserState();
  const favourites = useWuiFavourites(me.id);
  // Cards or the table (the cards amendment): the viewer's choice, per
  // browser, cards by default.
  const [view, setView] = useWuiView();
  // The tools (amendment 2) — about this visit, so plain state: which App
  // (a slug, or "" for all), what the search box holds, and the order
  // within a section. The App sections themselves stay (the author's
  // 「這樣可以」 on them).
  const [appFilter, setAppFilter] = useState("");
  // 「我的」: only the pages of items the viewer OWNS (owner, not deployer) —
  // a filter, not a section (the author), stacking with the App chips.
  const [mineOnly, setMineOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"newest" | "title">("newest");
  const apps = useApps();
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: qk.wuiOverview,
    queryFn: () => client.list(),
  });

  // A read that failed is a sentence and a way to try again — not the loading
  // line forever, which is what `isLoading || !data` rendered on a rejected
  // list: indistinguishable from a slow one, with nothing to press.
  if (isError) {
    return (
      <div className="page">
        <h1>WUI</h1>
        <p className="error" role="alert">
          {t("wui.error")}{" "}
          {/* `className="btn"`: base.css resets every button to bare text, so
              without it `data-size` styles nothing and this read as a word in
              the sentence, in the sentence's red. */}
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            onClick={() => void refetch()}
          >
            {t("wui.retry")}
          </button>
        </p>
      </div>
    );
  }
  if (isPending || !data) return <p>{t("wui.loading")}</p>;

  // The Apps present, in the listing's order (first seen) — the chips, and
  // the sections' order.
  const slugs = [...new Set(data.map((page) => page.slug))];
  // Search: the page title or the item title, case-insensitive substring.
  const needle = query.trim().toLocaleLowerCase();
  const matches = (page: DeployedWui) =>
    (!appFilter || page.slug === appFilter) &&
    (!mineOnly || (me.ready && page.item_owner === me.id)) &&
    (!needle ||
      page.title.toLocaleLowerCase().includes(needle) ||
      (page.item_title || page.item_id).toLocaleLowerCase().includes(needle));
  // Sort within a section: the server's order (newest Deploy first) stands
  // unless the viewer asked for names — `localeCompare`, so 出貨 sorts among
  // CJK the way the shell's language does, not by code point.
  const ordered = (pages: DeployedWui[]) =>
    sort === "title" ? [...pages].sort((a, b) => a.title.localeCompare(b.title, "zh-TW")) : pages;
  const shown = data.filter(matches);
  // Group by app, in first-seen order — the rows arrive newest first, so an
  // app whose latest Deploy is the most recent heads the page. A section
  // with nothing left after the filter is not drawn.
  const groups = new Map<string, DeployedWui[]>();
  for (const page of shown) {
    const rows = groups.get(page.slug);
    if (rows) rows.push(page);
    else groups.set(page.slug, [page]);
  }
  // The favourites group: the listed rows the viewer starred, in the
  // listing's order — a shortcut above the complete listing, never instead of
  // it, and under the same search, sort and filter. A starred key the
  // listing no longer returns draws nothing (and stays in storage: Deploying
  // the page again brings it back starred).
  const starred = shown.filter((page) => favourites.has(favouriteKey(page)));

  const rowProps = (page: DeployedWui, starred: boolean, showApp: boolean) => ({
    key: `${page.item_id}${page.path}`,
    page,
    client,
    starred,
    starReady: me.ready,
    onStar: () => favourites.toggle(favouriteKey(page)),
    // The App's tag on the element only where no section heading says the
    // App — the favourites section (the author: 「卡片裡面不用有標籤 我的最愛的
    // 可以有」).
    showApp,
  });
  // One list element per section, in whichever view is on. The two views
  // share `PageActions` (the star, Remove, the confirm, the mutation) and
  // `PageDetail` (the item link, who and when) — the parity between a card
  // and a row is structural, not two copies kept alike by hand.
  const listOf = (pages: DeployedWui[], starredAll: boolean, showApp: boolean) =>
    view === "cards" ? (
      <ul className="wui-cards">
        {ordered(pages).map((page) => (
          <PageCard {...rowProps(page, starredAll || favourites.has(favouriteKey(page)), showApp)} />
        ))}
      </ul>
    ) : (
      /* `wui-list`, not the bare `.page ul`: the narrow-viewport reflow in
         my-resources.css is written per list class. */
      <ul className="wui-list">
        {ordered(pages).map((page) => (
          <PageRow {...rowProps(page, starredAll || favourites.has(favouriteKey(page)), showApp)} />
        ))}
      </ul>
    );
  // Nothing Deployed at all: the empty state, no switch, no tools. Nothing
  // matching the tools: the tools stay (they are what to change) and one
  // line says so.
  const nothingDeployed = data.length === 0;

  return (
    // Cards need the Launcher's width for three to fit; the table keeps the
    // shell's 760. The modifier follows the view, not the other way round.
    <div className={view === "cards" && !nothingDeployed ? "page page--wide" : "page"}>
      <div className="page-head">
        <h1>WUI</h1>
        {nothingDeployed ? null : <ViewSwitch view={view} onChange={setView} />}
      </div>
      {nothingDeployed ? (
        <>
          <p className="empty">{t("wui.empty")}</p>
          <p className="hint">
            {t("wui.empty.what")} <Link to="/help">{t("wui.empty.help")}</Link>
          </p>
        </>
      ) : (
        <>
          <div className="page-tools">
            {/* One option per App present, the App's own name (`AppTag`'s
                rule: the slug until the manifests arrive), 全部 first. A
                select, not chips: twelve Apps as chips is two or three rows
                (the author: 「如果我們有 12 個 app 上面 filter 不就會擠爆」). */}
            <label>
              {t("wui.filter.app")}
              <select
                value={appFilter}
                onChange={(e) => setAppFilter(e.target.value)}
                aria-label={t("wui.filter.app")}
              >
                <option value="">{t("wui.filter.all")}</option>
                {slugs.map((slug) => (
                  <option key={slug} value={slug}>
                    {apps.find((a) => a.slug === slug)?.title || slug}
                  </option>
                ))}
              </select>
            </label>
            {/* Held until the identity has settled: the placeholder id must
                not claim anyone's items. */}
            <Chip on={mineOnly} onClick={() => setMineOnly((v) => !v)} disabled={!me.ready}>
              {t("wui.mine")}
            </Chip>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("wui.search")}
              aria-label={t("wui.search")}
            />
            <label>
              {t("wui.sort")}
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value === "title" ? "title" : "newest")}
                aria-label={t("wui.sort")}
              >
                <option value="newest">{t("wui.sort.newest")}</option>
                <option value="title">{t("wui.sort.title")}</option>
              </select>
            </label>
          </div>
          {shown.length === 0 ? <p className="empty">{t("wui.nomatch")}</p> : null}
          {starred.length > 0 ? (
            <section aria-labelledby="wui-favourites">
              <h2 id="wui-favourites">{t("wui.favourites")}</h2>
              {listOf(starred, true, true)}
            </section>
          ) : null}
          {[...groups].map(([slug, rows]) => {
            // The heading IS the pill: its label is the App's own name, the
            // slug until the manifests arrive (or for an App no longer
            // registered), and the icon inside is `aria-hidden` — so the
            // region is named by the same words a reader sees, and a group
            // can be landed on.
            return (
              <section key={slug} aria-labelledby={`wui-app-${slug}`}>
                <h2 id={`wui-app-${slug}`}>
                  <AppTag slug={slug} />
                </h2>
                {listOf(rows, false, false)}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

/** Cards or the table — ONE switch (the author: 「選取應該是 switch 或是類似的
 * ui」), off = cards, on = table, both words on it so either end names its
 * state. The `.switch` markup and classes are `components/Switch.tsx`'s
 * (base.css draws the track and the thumb); it is not that component only
 * because that one takes a single trailing word. */
function ViewSwitch({ view, onChange }: { view: WuiView; onChange: (v: WuiView) => void }) {
  const t = useT();
  const table = view === "table";
  return (
    <label className="switch view-switch" title={t("wui.view.tip")}>
      <span className="switch-label" data-on={!table}>
        {t("wui.view.cards")}
      </span>
      <input
        type="checkbox"
        role="switch"
        checked={table}
        aria-label={t("wui.view.label")}
        onChange={(e) => onChange(e.target.checked ? "table" : "cards")}
      />
      <span className="switch-track" aria-hidden="true">
        <span className="switch-thumb" />
      </span>
      <span className="switch-label" data-on={table}>
        {t("wui.view.table")}
      </span>
    </label>
  );
}

/** A filter chip — `LanguageToggle`'s button, `aria-pressed` the state. */
function Chip({
  on,
  onClick,
  disabled,
  children,
}: {
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      style={{
        padding: "6px 12px",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-btn)",
        fontSize: pxToRem(12),
        background: on ? "var(--accent-soft)" : "var(--white)",
        color: on ? "var(--accent-h)" : "var(--text-paper)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

type PageProps = {
  page: DeployedWui;
  client: WuiApi;
  /** Whether THIS viewer starred it — the page's one set, so a page drawn
   * twice (its App group and the favourites group) shows one answer. */
  starred: boolean;
  /** False until the viewer's identity has settled; the star is held so a
   * press cannot be filed under the placeholder id. */
  starReady: boolean;
  onStar: () => void;
  /** Draw the App's tag on the element — only where no section heading says
   * the App (the favourites section). */
  showApp: boolean;
};

/** The Remove mutation and its confirm — PER ELEMENT (the pattern
 * `LiveEnvironmentRow` set): one page's pending press must not disable every
 * other page's button. */
function useRemove(page: DeployedWui, client: WuiApi) {
  const t = useT();
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const remove = useMutation({
    mutationFn: () => client.remove(page.slug, page.item_id, page.path),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.wuiOverview }),
    // The element renders its own failure; without this the query client
    // ALSO routes it to the app-wide write-failure notice, so one press
    // raises two messages — and the page-level one names no page.
    meta: { silentError: true },
  });
  const ask = () => {
    // Asked once, and the question names the page: a mistaken press costs
    // one Deploy to undo, but the title is what makes it clear WHICH page is
    // about to go — and that the page itself stays.
    void confirm({
      title: t("wui.remove.title", { title: page.title }),
      body: t("wui.remove.body"),
      actions: [
        { id: "cancel", label: t("wui.remove.cancel") },
        { id: "remove", label: t("wui.remove"), variant: "danger" },
      ],
    }).then((choice) => {
      if (choice === "remove") remove.mutate();
    });
  };
  return { remove, ask };
}

/** The star. The viewer's own, so every page has one: a reader who may not
 * unlist may still keep a favourite. `aria-pressed` is the state and what
 * the sheet fills the glyph from; the label is the action, naming the page,
 * so "pressed" is never the only clue. `ghost`: it must not read as a
 * second 下架. `className` lets the card place it (top-right). */
function StarButton({
  page,
  starred,
  starReady,
  onStar,
  className = "btn",
}: Pick<PageProps, "page" | "starred" | "starReady" | "onStar"> & { className?: string }) {
  const t = useT();
  return (
    <button
      type="button"
      className={className}
      data-variant="ghost"
      data-size="sm"
      aria-pressed={starred}
      aria-label={starred ? t("wui.unstar", { title: page.title }) : t("wui.star", { title: page.title })}
      disabled={!starReady}
      onClick={onStar}
    >
      <Icon name="star" size={16} />
    </button>
  );
}

/** 下架 — for someone who may. The word is the opposite of Deploy's 上架
 * and the tooltip says what stays, because "移除" read as "delete". */
function UnlistButton({
  page,
  remove,
  onRemove,
}: {
  page: DeployedWui;
  remove: ReturnType<typeof useRemove>["remove"];
  onRemove: () => void;
}) {
  const t = useT();
  if (!page.can_remove) return null;
  return (
    <button
      type="button"
      className="btn"
      data-variant="secondary"
      data-size="sm"
      aria-label={`${t("wui.remove")} ${page.title}`}
      title={t("wui.remove.tip")}
      disabled={remove.isPending}
      onClick={onRemove}
    >
      {t("wui.remove")}
    </button>
  );
}

/** Which item the page came from (and, in the table, whose it is), who put
 * it up and when — one line, cut with an ellipsis when it does not fit (the
 * author: 「不要硬要顯示全部」), the whole sentence in the tooltip. The card
 * names the owner in its footer instead, so it asks for the line without. */
function PageDetail({
  page,
  withOwner,
  withApp = false,
}: {
  page: DeployedWui;
  withOwner: boolean;
  /** The App's tag at the start of the line — the table's way of saying the
   * App where its section heading does not. */
  withApp?: boolean;
}) {
  const t = useT();
  const owner = useUser(page.item_owner);
  const item = page.item_title || page.item_id;
  const by = t("wui.row.by", {
    who: page.deployed_by,
    when: relativeTime(new Date(page.deployed_at).toISOString()),
  });
  const sentence = withOwner ? `${item} · ${owner.name} · ${by}` : `${item} · ${by}`;
  return (
    <span className="detail" title={sentence}>
      {withApp ? (
        <>
          <AppTag slug={page.slug} />{" "}
        </>
      ) : null}
      {/* The workspace has no deep link to a file, so this opens the item. */}
      <Link to={`/a/${page.slug}/${page.item_id}`}>{item}</Link>
      {withOwner ? (
        <>
          {" · "}
          <UserChip userId={page.item_owner} nameOnly />
        </>
      ) : null}
      {" · "}
      {/* Relative, like the rest of the shell, with the exact stamp in the
          title — `relativeTime` / `exactTime` are the shell's own pair
          (`GroupsPage`), and the sentence template is written for the
          relative form ("2 d ago" / "just now" / "7 Aug"). */}
      <span title={exactTime(new Date(page.deployed_at).toISOString())}>{by}</span>
    </span>
  );
}

/** One Deployed page as a TABLE row: mark · title · (App) · detail · star ·
 * 下架. */
function PageRow({ page, client, starred, starReady, onStar, showApp }: PageProps) {
  const t = useT();
  const { remove, ask } = useRemove(page, client);
  return (
    <li>
      {/* The page's own icon, or the title's letters: one circle per row
          (`plan-wui-overview-icon-favourites`). Decoration — the link is the
          row's name. */}
      <PageMark
        slug={page.slug}
        itemId={page.item_id}
        path={page.path}
        icon={page.icon}
        color={page.color}
        title={page.title}
      />
      {/* A new tab: the reader page renders outside the shell, with no way
          back to here, so it opens beside the overview rather than over it. */}
      <a href={wuiAddress(page.slug, page.item_id, page.path)} target="_blank" rel="noopener" title={page.title}>
        {page.title}
      </a>
      <PageDetail page={page} withOwner withApp={showApp} />
      <StarButton page={page} starred={starred} starReady={starReady} onStar={onStar} />
      <UnlistButton page={page} remove={remove} onRemove={ask} />
      {remove.isError ? (
        <span className="error" role="alert">
          {t("wui.remove.failed")}
        </span>
      ) : null}
    </li>
  );
}

/** One Deployed page as a CARD — `AppCard`'s shape (Launcher): a stripe in
 * the App's colour, the mark, the title (two lines at most), one muted line
 * (one line, cut). The WHOLE card is the page's link: the sheet stretches
 * the title's `<a>` over the card (`::after`), and the star (top-right,
 * where a favourite usually is), 下架 (the footer) and the item link sit
 * above it (`z-index`), so they press without opening the page and are
 * never inside the link. */
function PageCard({ page, client, starred, starReady, onStar, showApp }: PageProps) {
  const t = useT();
  const { remove, ask } = useRemove(page, client);
  // The card's colour for the stripe and its tint for the hover: the page's
  // own when its view file declared one the sheet can draw (the author:
  // 「顏色應該可以讓 deploy 決定」), else the App's — the same palette the
  // heading's pill and the mark resolve. Hex custom properties, never
  // `oklch()` inline (happy-dom drops it).
  const app = useApps().find((a) => a.slug === page.slug);
  const colour = pageColour(page.color, app?.color);
  const palette = appTagPalette(colour);
  return (
    <li
      className="wui-card"
      style={
        {
          ...(colour ? { "--app-color": colour } : {}),
          ...(palette ? { "--app-tint": palette.tint } : {}),
        } as React.CSSProperties
      }
    >
      <span className="stripe" aria-hidden="true" />
      <div className="wui-card-body">
        <PageMark
          slug={page.slug}
          itemId={page.item_id}
          path={page.path}
          icon={page.icon}
          color={page.color}
          title={page.title}
          size={54}
        />
        <div className="wui-card-text">
          <a href={wuiAddress(page.slug, page.item_id, page.path)} target="_blank" rel="noopener" title={page.title}>
            {page.title}
          </a>
          <PageDetail page={page} withOwner={false} />
        </div>
      </div>
      <StarButton page={page} starred={starred} starReady={starReady} onStar={onStar} className="btn star" />
      {/* The footer: on the left the item's OWNER (the author: 「Owner 也要在
          上面」) and — only where the section heading does not say it — the
          App's tag; on the right 下架 for someone who may. */}
      <div className="wui-card-foot">
        <span className="wui-card-who">
          {showApp ? <AppTag slug={page.slug} /> : null}
          <UserChip userId={page.item_owner} size={18} />
        </span>
        <UnlistButton page={page} remove={remove} onRemove={ask} />
      </div>
      {remove.isError ? (
        <span className="error" role="alert">
          {t("wui.remove.failed")}
        </span>
      ) : null}
    </li>
  );
}
