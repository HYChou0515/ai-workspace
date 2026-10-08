/**
 * `/skill-hub` — every skill the viewer may read, in one place
 * (`docs/plan-skill-hub.md`, laid out by `docs/plan-skill-hub-ux-redo.md`).
 *
 * A compact list (D1, NN/g "List vs. Grid": text to compare, no images): one
 * row per skill, the name and a one-line description on the left, owner,
 * counts and the update day on the right. Browsing lists originals, each with
 * its fork count; a search, 「我的」 or an owner lists forks beside them, each
 * naming its original (D2). The rows, the visibility and every filter are the
 * server's (`GET /skill-hub/entries`); the filters live in the address, so
 * Back from a skill lands on the same list (D4). Fifty at a time, with
 * 「載入更多」 and the total (D4, NN/g "Infinite Scrolling": the footer stays
 * reachable).
 *
 * Nothing here installs, downloads or publishes: installing is the skill's
 * page (D6) or the workspace's Skills panel.
 */

import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { qk } from "../api/queryKeys";
import { type SkillHubApi, type SkillHubSort, skillHubApi } from "../api/skillHub";
import { PageNotice, type PageNoticeContent } from "../components/PageNotice";
import { SkillHubRow } from "../components/SkillHubRow";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useUser } from "../hooks/useUsers";
import { useT } from "../lib/i18n";

const SORTS: readonly SkillHubSort[] = ["name", "popular", "updated"];
const SORT_LABEL = {
  name: "skillHub.sort.name",
  popular: "skillHub.sort.popular",
  updated: "skillHub.sort.updated",
} as const;
const PAGE = 50;

export function SkillHubPage({
  client = skillHubApi,
}: {
  client?: SkillHubApi;
}) {
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: "Skill hub" }]);
  // What the page that sent us here wants said — the entry page after a
  // transfer or a delete (plan-skill-hub-ui-polish D10). Router state, so a
  // reload or a fresh visit carries no stale notice — and consumed on
  // arrival: kept for this mount, cleared from the history entry, so Back to
  // the list does not announce it again (review round 1 of #826).
  const location = useLocation();
  const navigate = useNavigate();
  const [notice] = useState<PageNoticeContent | null>(
    () => (location.state as { notice?: PageNoticeContent } | null)?.notice ?? null,
  );
  useEffect(() => {
    if ((location.state as { notice?: PageNoticeContent } | null)?.notice) {
      navigate(location.pathname + location.search + location.hash, {
        replace: true,
        state: null,
      });
    }
    // Once, on arrival: the notice is what THIS mount was handed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The filters are the address's (D4): a reload, a shared link and Back
  // all land on the same list. Replaced, not pushed — a filter change is not
  // a page the person means to go back through one by one.
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const mine = params.get("mine") === "1";
  const owner = params.get("owner") ?? "";
  const sortParam = params.get("sort") as SkillHubSort | null;
  const sort: SkillHubSort = sortParam && SORTS.includes(sortParam) ? sortParam : "name";
  const setFilter = (next: Record<string, string>) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(next)) {
          if (v) out.set(k, v);
          else out.delete(k);
        }
        return out;
      },
      { replace: true },
    );

  // The search is the server's (it matches what the agent's search tool
  // matches), so the box is debounced rather than sent per keystroke — the
  // review inbox's idiom. The box holds what is typed; the address what was
  // searched.
  const [query, setQuery] = useState(q);
  useEffect(() => {
    const id = setTimeout(() => {
      if (query.trim() !== q) setFilter({ q: query.trim() });
    }, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const {
    data,
    isPending,
    isError,
    isFetching,
    isFetchingNextPage,
    isPlaceholderData,
    errorUpdateCount,
    refetch,
    fetchNextPage,
    hasNextPage,
  } = useInfiniteQuery({
    queryKey: qk.skillHubBrowse(q, mine, owner, sort),
    queryFn: ({ pageParam }) =>
      client.browse({ q, mine, owner, sort, offset: pageParam, limit: PAGE }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.entries.length, 0);
      return loaded < last.total && last.entries.length > 0 ? loaded : undefined;
    },
    // A new filter is a new query key, and without this every keystroke's
    // debounce made the page `isPending` — the whole tree, search box
    // included, was swapped for 載入中…, the box remounted, and the caret
    // and focus went with it (plan-skill-hub-ui-polish D2). The previous
    // list stays on screen until the next one lands; only the FIRST load
    // has nothing to show.
    placeholderData: keepPreviousData,
  });

  // Only the UNTOUCHED page's first load has nothing to show. Once the tools
  // were used they stay mounted whatever the list does — loading and failure
  // are states of the results area, not of the tree (review rounds 1 and 2
  // of #826). Retry after a failed first load is a refetch of a data-less
  // errored query, which TanStack reports as `pending` again — so "never
  // settled" means no result AND no error so far (round 3 of #826).
  const untouched = !q && !mine && !owner;
  const neverSettled = isPending && !isError && errorUpdateCount === 0;
  if (neverSettled && untouched) return <p>{t("skillHub.loading")}</p>;
  const pages = data?.pages ?? [];
  const rows = pages.flatMap((p) => p.entries);
  const total = pages[0]?.total ?? 0;
  const countedSince = pages[0]?.counted_since ?? "";
  // Not while an error is shown: a failed refetch of an empty hub keeps the
  // empty page as data, and the empty state would hide the error and Retry.
  const nothingPublished =
    untouched && !isError && !isPlaceholderData && data !== undefined && total === 0;

  return (
    <div className="page skill-hub-page">
      <h1>Skill hub</h1>
      <PageNotice notice={notice} />
      {nothingPublished ? (
        <>
          <p className="empty">{t("skillHub.empty")}</p>
          <p className="hint">{t("skillHub.empty.what")}</p>
        </>
      ) : (
        <>
          <div className="page-tools">
            <input
              className="input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("skillHub.search")}
              aria-label={t("skillHub.search")}
            />
            <div className="skill-hub-scope" role="group" aria-label={t("skillHub.show")}>
              <span className="skill-hub-tool-label" aria-hidden="true">
                {t("skillHub.show")}
              </span>
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant={mine ? "secondary" : "primary"}
                aria-pressed={!mine}
                onClick={() => setFilter({ mine: "" })}
              >
                {t("skillHub.all")}
              </button>
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant={mine ? "primary" : "secondary"}
                aria-pressed={mine}
                onClick={() => setFilter({ mine: "1", owner: "" })}
              >
                {t("skillHub.mine")}
              </button>
            </div>
            <label className="skill-hub-sort">
              <span className="skill-hub-tool-label">{t("skillHub.sort")}</span>
              <select
                className="input"
                value={sort}
                onChange={(e) =>
                  setFilter({ sort: e.target.value === "name" ? "" : e.target.value })
                }
              >
                {SORTS.map((s) => (
                  <option key={s} value={s}>
                    {t(SORT_LABEL[s])}
                  </option>
                ))}
              </select>
            </label>
            {owner ? <OwnerFilter owner={owner} onClear={() => setFilter({ owner: "" })} /> : null}
          </div>
          {/* The results area: busy while the next list is on its way (the
              previous one stays visible, dimmed), the error with Retry when
              a search failed, else the rows. */}
          <div
            className="skill-hub-results"
            data-testid="skill-hub-results"
            aria-busy={isFetching && isPlaceholderData ? "true" : "false"}
          >
            {isError ? (
              <p className="error" role="alert">
                {t("skillHub.error")}{" "}
                <button
                  type="button"
                  className="btn"
                  data-variant="secondary"
                  data-size="sm"
                  onClick={() => void refetch()}
                >
                  {t("skillHub.retry")}
                </button>
              </p>
            ) : isPending ? (
              <p className="loading">{t("skillHub.loading")}</p>
            ) : rows.length === 0 ? (
              <div className="empty">
                <p>{q ? t("skillHub.noMatch.query", { q }) : t("skillHub.noMatch")}</p>
                <button
                  type="button"
                  className="btn"
                  data-variant="secondary"
                  data-size="sm"
                  onClick={() => {
                    setQuery("");
                    setFilter(q ? { q: "" } : { q: "", mine: "", owner: "" });
                  }}
                >
                  {q ? t("skillHub.clearSearch") : t("skillHub.clearFilters")}
                </button>
              </div>
            ) : (
              <>
                <p className="muted small skill-hub-summary">
                  <span>{t("skillHub.total", { count: total })}</span>
                  {countedSince ? (
                    <span>{t("skillHub.countedSince", { day: countedSince })}</span>
                  ) : null}
                </p>
                <ul className="skill-hub-list">
                  {rows.map((entry) => (
                    <SkillHubRow
                      key={entry.id}
                      entry={entry}
                      onOwner={(o) => setFilter({ owner: o, mine: "" })}
                    />
                  ))}
                </ul>
                <div className="skill-hub-more">
                  {rows.length < total ? (
                    <span className="muted small">
                      {t("skillHub.shown", { shown: rows.length, total })}
                    </span>
                  ) : null}
                  {hasNextPage ? (
                    <button
                      type="button"
                      className="btn"
                      data-variant="secondary"
                      disabled={isFetchingNextPage}
                      onClick={() => void fetchNextPage()}
                    >
                      {isFetchingNextPage ? t("skillHub.loading") : t("skillHub.loadMore")}
                    </button>
                  ) : null}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function OwnerFilter({ owner, onClear }: { owner: string; onClear: () => void }) {
  const t = useT();
  const name = useUser(owner).name;
  return (
    <span className="skill-hub-filter-chip">
      {t("skillHub.owner.filter", { name })}
      <button
        type="button"
        className="btn"
        data-variant="ghost"
        data-size="sm"
        aria-label={t("skillHub.owner.clear")}
        onClick={onClear}
      >
        ×
      </button>
    </span>
  );
}
