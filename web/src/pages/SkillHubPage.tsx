/**
 * `/skill-hub` — every skill the viewer may read, in one place
 * (`docs/plan-skill-hub.md`).
 *
 * The rows are the server's, and so is the visibility: `GET /skill-hub/entries`
 * returns exactly the entries this viewer may read, roots with their forks
 * beneath (plan Q4: 根在上、fork 收在原作底下). Search (name / description)
 * and 「我的」 are server parameters too — one rule in `SkillHubStore.visible`
 * for the page, the search tool and the install door alike.
 *
 * Nothing here installs, downloads or publishes (D2): those are the item's,
 * in its Skills panel. A row opens the entry's page; that page holds the
 * owner's actions and nobody else's.
 */

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { qk } from "../api/queryKeys";
import {
  type SkillHubApi,
  type SkillHubCard,
  skillHubApi,
} from "../api/skillHub";
import { AppTag } from "../components/AppTag";
import { PageNotice, type PageNoticeContent } from "../components/PageNotice";
import { UserChip } from "../components/UserChip";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useT } from "../lib/i18n";

export function SkillHubPage({
  client = skillHubApi,
}: {
  client?: SkillHubApi;
}) {
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: "Skill hub" }]);
  // What the page that sent us here wants said — the entry page after a
  // transfer or a delete (plan-skill-hub-ui-polish D10). Router state, so a
  // reload or a fresh visit carries no stale notice.
  const notice =
    (useLocation().state as { notice?: PageNoticeContent } | null)?.notice ??
    null;
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState(false);
  // The search is the server's (it matches what the agent's search tool
  // matches), so the box is debounced rather than sent per keystroke — the
  // review inbox's idiom.
  const [q, setQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setQ(query.trim()), 250);
    return () => clearTimeout(id);
  }, [query]);
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: qk.skillHub(q, mine),
    queryFn: () => client.list(q, mine),
    // A new (q, mine) is a new query key, and without this every keystroke's
    // debounce made the page `isPending` — the whole tree, search box
    // included, was swapped for 載入中…, the box remounted, and the caret
    // and focus went with it (plan-skill-hub-ui-polish D2). The previous
    // list stays on screen until the next one lands; only the FIRST load
    // has nothing to show.
    placeholderData: keepPreviousData,
  });
  // "Nothing published at all" and "nothing matches the tools" are different
  // states: the first gets the empty state (no tools, they would filter
  // nothing); the second keeps the tools, because they are what to change.
  const { data: everything } = useQuery({
    queryKey: qk.skillHub("", false),
    queryFn: () => client.list("", false),
  });

  if (isError) {
    return (
      <div className="page">
        <h1>Skill hub</h1>
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
      </div>
    );
  }
  if (isPending || !data) return <p>{t("skillHub.loading")}</p>;

  const nothingPublished =
    everything !== undefined && everything.length === 0 && !q && !mine;
  // Where a fork's root is — by id, from the one listing that always holds
  // every entry this viewer may read (`everything`): a fork shown on its own
  // (「我的」, or a search that matched the fork and not the root) can still
  // say whose it is. A root that is in neither is one the viewer cannot read.
  const lineage = new Map<string, SkillHubCard>();
  for (const e of everything ?? data) {
    lineage.set(e.id, e);
    for (const f of e.forks) lineage.set(f.id, f);
  }

  return (
    <div className="page">
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
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("skillHub.search")}
              aria-label={t("skillHub.search")}
            />
            <div
              className="skill-hub-scope"
              role="group"
              aria-label={t("skillHub.mine")}
            >
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant={mine ? "secondary" : "primary"}
                aria-pressed={!mine}
                onClick={() => setMine(false)}
              >
                {t("skillHub.all")}
              </button>
              <button
                type="button"
                className="btn"
                data-size="sm"
                data-variant={mine ? "primary" : "secondary"}
                aria-pressed={mine}
                onClick={() => setMine(true)}
              >
                {t("skillHub.mine")}
              </button>
            </div>
          </div>
          {data.length === 0 ? (
            <p className="empty">{t("skillHub.noMatch")}</p>
          ) : (
            <ul className="skill-hub-list">
              {data.map((entry) => (
                <SkillRow key={entry.id} entry={entry} lineage={lineage} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/** A root with its forks beneath (one level), or a fork standing on its own
 * when its root is out of view. */
function SkillRow({
  entry,
  fork = false,
  lineage,
}: {
  entry: SkillHubCard;
  fork?: boolean;
  lineage: Map<string, SkillHubCard>;
}) {
  const t = useT();
  const forks = entry.forks.length;
  // Every fork says where it came from, whether it sits under its root or
  // stands alone (plan-skill-hub-ui-polish D9). The review badge is gone
  // (D7): every entry was reviewed, so "has notes" separated nothing worth a
  // badge — the notes themselves are on the entry's page.
  const root = entry.forked_from ? lineage.get(entry.forked_from) : undefined;
  return (
    <li
      className="skill-hub-row"
      data-fork={fork || undefined}
      data-testid={`entry-${entry.id}`}
    >
      <div className="skill-hub-row-head">
        <Link
          to={`/skill-hub/${encodeURIComponent(entry.id)}`}
          className="skill-hub-row-title"
        >
          <span className="skill-hub-owner">{entry.owner}/</span>
          {entry.name}
        </Link>
        <AppTag slug={entry.source_app} />
        {forks > 0 ? (
          <span className="skill-hub-badge" data-kind="forks">
            {forks === 1
              ? t("skillHub.fork.one")
              : t("skillHub.forks", { count: forks })}
          </span>
        ) : null}
      </div>
      <p className="skill-hub-row-desc">{entry.description}</p>
      {entry.forked_from ? (
        <p className="skill-hub-row-origin">
          {root
            ? t("skillHub.forkOf", { origin: `${root.owner}/${root.name}` })
            : t("skillHub.forkOf.gone")}
        </p>
      ) : null}
      <div className="skill-hub-row-meta">
        <UserChip userId={entry.owner} size={18} nameOnly />
      </div>
      {forks > 0 ? (
        <ul className="skill-hub-forks">
          {entry.forks.map((f) => (
            <SkillRow key={f.id} entry={f} fork lineage={lineage} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
