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
import { useT } from "../lib/i18n";

export function WuiOverviewPage({ client = wuiApi }: { client?: WuiApi }) {
  const t = useT();
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

  return (
    <div className="page">
      <h1>WUI</h1>
      {groups.size === 0 ? (
        <>
          <p className="empty">{t("wui.empty")}</p>
          <p className="hint">
            {t("wui.empty.what")} <Link to="/help">{t("wui.empty.help")}</Link>
          </p>
        </>
      ) : (
        [...groups].map(([slug, rows]) => {
          // The heading IS the pill: its label is the App's own name, the slug
          // until the manifests arrive (or for an App no longer registered),
          // and the icon inside is `aria-hidden` — so the region is named by
          // the same words a reader sees, and a group can be landed on.
          return (
            <section key={slug} aria-labelledby={`wui-app-${slug}`}>
              <h2 id={`wui-app-${slug}`}>
                <AppTag slug={slug} />
              </h2>
              {/* `wui-list`, not the bare `.page ul`: the narrow-viewport reflow
                  in my-resources.css is written per list class. */}
              <ul className="wui-list">
                {rows.map((page) => (
                  <PageRow key={`${page.item_id}${page.path}`} page={page} client={client} />
                ))}
              </ul>
            </section>
          );
        })
      )}
    </div>
  );
}

/** One Deployed page: where it opens, which item it came from, who put it up,
 * and — for someone who may — Remove. Its own component so the mutation is the
 * ROW'S (the pattern `LiveEnvironmentRow` set): one row's pending press must
 * not disable every other row's button. */
function PageRow({ page, client }: { page: DeployedWui; client: WuiApi }) {
  const t = useT();
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const remove = useMutation({
    mutationFn: () => client.remove(page.slug, page.item_id, page.path),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.wuiOverview }),
    // The row renders its own failure below; without this the query client
    // ALSO routes it to the app-wide write-failure notice, so one press raises
    // two messages — and the page-level one names no page.
    meta: { silentError: true },
  });
  return (
    <li>
      {/* A new tab: the reader page renders outside the shell, with no way
          back to here, so it opens beside the overview rather than over it. */}
      <a href={wuiAddress(page.slug, page.item_id, page.path)} target="_blank" rel="noopener">
        {page.title}
      </a>
      <span className="detail">
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
      </span>
      {page.can_remove ? (
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          aria-label={`${t("wui.remove")} ${page.title}`}
          disabled={remove.isPending}
          onClick={() => {
            // Asked once, and the question names the page: a mistaken press
            // costs one Deploy to undo, but the row's title is what makes it
            // clear WHICH page is about to go — and that the page itself stays.
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
          }}
        >
          {t("wui.remove")}
        </button>
      ) : null}
      {remove.isError ? (
        <span className="error" role="alert">
          {t("wui.remove.failed")}
        </span>
      ) : null}
    </li>
  );
}
