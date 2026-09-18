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
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import { exactTime, relativeTime } from "../api/types";
import { type DeployedWui, type WuiApi, wuiAddress, wuiApi } from "../api/wui";
import { AppTag } from "../components/AppTag";
import { useDialog } from "../components/Dialog";
import { Icon } from "../components/Icon";
import { PageMark } from "../components/PageMark";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { useT } from "../lib/i18n";
import { useApps } from "../hooks/useResources";
import { appTagPalette } from "../lib/appColor";
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

  // Group by app, in first-seen order — the rows arrive newest first, so an
  // app whose latest Deploy is the most recent heads the page. Within a group
  // the server's order stands; nothing here re-sorts.
  const groups = new Map<string, DeployedWui[]>();
  for (const page of data) {
    const rows = groups.get(page.slug);
    if (rows) rows.push(page);
    else groups.set(page.slug, [page]);
  }
  // The favourites group: the listed rows the viewer starred, in the
  // listing's order — a shortcut above the complete listing, never instead of
  // it. A starred key the listing no longer returns draws nothing (and stays
  // in storage: Deploying the page again brings it back starred).
  const starred = data.filter((page) => favourites.has(favouriteKey(page)));

  const rowProps = (page: DeployedWui, starred: boolean) => ({
    key: `${page.item_id}${page.path}`,
    page,
    client,
    starred,
    starReady: me.ready,
    onStar: () => favourites.toggle(favouriteKey(page)),
  });
  // One list element per section, in whichever view is on. The two views
  // share `PageActions` (the star, Remove, the confirm, the mutation) and
  // `PageDetail` (the item link, who and when) — the parity between a card
  // and a row is structural, not two copies kept alike by hand.
  const listOf = (pages: DeployedWui[], starredAll: boolean) =>
    view === "cards" ? (
      <ul className="wui-cards">
        {pages.map((page) => (
          <PageCard {...rowProps(page, starredAll || favourites.has(favouriteKey(page)))} />
        ))}
      </ul>
    ) : (
      /* `wui-list`, not the bare `.page ul`: the narrow-viewport reflow in
         my-resources.css is written per list class. */
      <ul className="wui-list">
        {pages.map((page) => (
          <PageRow {...rowProps(page, starredAll || favourites.has(favouriteKey(page)))} />
        ))}
      </ul>
    );

  return (
    // Cards need the Launcher's width for three to fit; the table keeps the
    // shell's 760. The modifier follows the view, not the other way round.
    <div className={view === "cards" && groups.size > 0 ? "page page--wide" : "page"}>
      <div className="page-head">
        <h1>WUI</h1>
        {/* No toggle on the empty state: there is nothing to draw either way. */}
        {groups.size > 0 ? <ViewToggle view={view} onChange={setView} /> : null}
      </div>
      {groups.size === 0 ? (
        <>
          <p className="empty">{t("wui.empty")}</p>
          <p className="hint">
            {t("wui.empty.what")} <Link to="/help">{t("wui.empty.help")}</Link>
          </p>
        </>
      ) : (
        <>
          {starred.length > 0 ? (
            <section aria-labelledby="wui-favourites">
              <h2 id="wui-favourites">{t("wui.favourites")}</h2>
              {listOf(starred, true)}
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
                {listOf(rows, false)}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

/** Cards or the table — `LanguageToggle`'s two-button shape: `aria-pressed`
 * is the state, the words are the product's. */
function ViewToggle({ view, onChange }: { view: WuiView; onChange: (v: WuiView) => void }) {
  const t = useT();
  const options: { id: WuiView; label: string }[] = [
    { id: "cards", label: t("wui.view.cards") },
    { id: "table", label: t("wui.view.table") },
  ];
  return (
    <div role="group" aria-label={t("wui.view.label")} style={{ display: "flex", gap: 6 }}>
      {options.map((o) => {
        const on = o.id === view;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.id)}
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
            {o.label}
          </button>
        );
      })}
    </div>
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

/** The star and — for someone who may — Remove. The star is the viewer's
 * own, so every page has one: a reader who may not Remove may still keep a
 * favourite. `aria-pressed` is the state and what the sheet fills the glyph
 * from; the label is the action, naming the page, so "pressed" is never the
 * only clue. `ghost`, not `secondary`: it sits beside Remove and must not
 * read as a second Remove. */
function PageActions({
  page,
  starred,
  starReady,
  onStar,
  remove,
  onRemove,
}: Pick<PageProps, "page" | "starred" | "starReady" | "onStar"> & {
  remove: ReturnType<typeof useRemove>["remove"];
  onRemove: () => void;
}) {
  const t = useT();
  return (
    <>
      <button
        type="button"
        className="btn"
        data-variant="ghost"
        data-size="sm"
        aria-pressed={starred}
        aria-label={starred ? t("wui.unstar", { title: page.title }) : t("wui.star", { title: page.title })}
        disabled={!starReady}
        onClick={onStar}
      >
        <Icon name="star" size={16} />
      </button>
      {page.can_remove ? (
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          aria-label={`${t("wui.remove")} ${page.title}`}
          disabled={remove.isPending}
          onClick={onRemove}
        >
          {t("wui.remove")}
        </button>
      ) : null}
    </>
  );
}

/** Which item the page came from, who put it up and when. */
function PageDetail({ page }: { page: DeployedWui }) {
  const t = useT();
  return (
    <>
      {/* The workspace has no deep link to a file, so this opens the item. */}
      <Link to={`/a/${page.slug}/${page.item_id}`}>{page.item_title || page.item_id}</Link>
      {" · "}
      {/* Relative, like the rest of the shell, with the exact stamp in the
          title — `relativeTime` / `exactTime` are the shell's own pair
          (`GroupsPage`), and the sentence template is written for the
          relative form ("2 d ago" / "just now" / "7 Aug"). */}
      <span title={exactTime(new Date(page.deployed_at).toISOString())}>
        {t("wui.row.by", {
          who: page.deployed_by,
          when: relativeTime(new Date(page.deployed_at).toISOString()),
        })}
      </span>
    </>
  );
}

/** One Deployed page as a TABLE row: mark · title · detail · star · Remove. */
function PageRow({ page, client, starred, starReady, onStar }: PageProps) {
  const t = useT();
  const { remove, ask } = useRemove(page, client);
  return (
    <li>
      {/* The page's own icon, or the title's letters: one circle per row
          (`plan-wui-overview-icon-favourites`). Decoration — the link is the
          row's name. */}
      <PageMark slug={page.slug} itemId={page.item_id} path={page.path} icon={page.icon} title={page.title} />
      {/* A new tab: the reader page renders outside the shell, with no way
          back to here, so it opens beside the overview rather than over it. */}
      <a href={wuiAddress(page.slug, page.item_id, page.path)} target="_blank" rel="noopener">
        {page.title}
      </a>
      <span className="detail">
        <PageDetail page={page} />
      </span>
      <PageActions
        page={page}
        starred={starred}
        starReady={starReady}
        onStar={onStar}
        remove={remove}
        onRemove={ask}
      />
      {remove.isError ? (
        <span className="error" role="alert">
          {t("wui.remove.failed")}
        </span>
      ) : null}
    </li>
  );
}

/** One Deployed page as a CARD — `AppCard`'s shape (Launcher): a stripe in
 * the App's colour, the mark, the title, one muted line. The WHOLE card is
 * the page's link: the sheet stretches the title's `<a>` over the card
 * (`::after`), and the actions and the item link sit above it (`z-index`),
 * so they press without opening the page and are never inside the link. */
function PageCard({ page, client, starred, starReady, onStar }: PageProps) {
  const t = useT();
  const { remove, ask } = useRemove(page, client);
  // The App's own colour for the stripe, and its tint for the hover — the
  // same palette the heading's pill and the mark resolve. Hex custom
  // properties, never `oklch()` inline (happy-dom drops it).
  const app = useApps().find((a) => a.slug === page.slug);
  const palette = appTagPalette(app?.color);
  return (
    <li
      className="wui-card"
      style={
        {
          ...(app?.color ? { "--app-color": app.color } : {}),
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
          title={page.title}
          size={54}
        />
        <div className="wui-card-text">
          <a href={wuiAddress(page.slug, page.item_id, page.path)} target="_blank" rel="noopener">
            {page.title}
          </a>
          <span className="detail">
            <PageDetail page={page} />
          </span>
        </div>
      </div>
      <div className="actions">
        <PageActions
          page={page}
          starred={starred}
          starReady={starReady}
          onStar={onStar}
          remove={remove}
          onRemove={ask}
        />
      </div>
      {remove.isError ? (
        <span className="error" role="alert">
          {t("wui.remove.failed")}
        </span>
      ) : null}
    </li>
  );
}
