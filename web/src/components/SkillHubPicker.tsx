/**
 * 「從 skill hub 裝」— pick a published skill and install it into THIS
 * workspace (`docs/plan-skill-hub.md` D2). A page of the Skills panel, with
 * a way back, rather than a modal over it (plan-skill-hub-ux-redo D9: a modal
 * on a modal).
 *
 * The rows are the skill hub list's own (`GET /skill-hub/entries`, the same
 * rules and the same 50 at a time), asked for with this workspace's App so
 * every row carries the告知 the plan requires (Q1/Q2): which of the tools it
 * mentions this App does not have — shown, never enforced. The install goes
 * through the panel's door (`POST …/skills/install`), which shares its core
 * and its refusals with the agent's `install_skill` tool.
 *
 * The refusal for a name already here is also known BEFORE the press
 * (plan-skill-hub-ui-polish D8): the panel hands over the names whose files
 * are here (`taken`, the same predicate as its Download), and such a row is
 * marked with its Install disabled — pinned to the route's own rule by a
 * parity test (`tests/api/test_skill_hub_panel.py`).
 */

import { keepPreviousData, useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import { type SkillHubApi, type SkillHubCard, skillHubApi } from "../api/skillHub";
import { useUser } from "../hooks/useUsers";
import { useT } from "../lib/i18n";
import { describeRefusal } from "../lib/skillHubRefusal";
import { Icon } from "./Icon";

const PAGE = 50;

export function SkillHubPicker({
  slug,
  itemId,
  taken,
  onInstalled,
  onBack,
  client = skillHubApi,
}: {
  slug: string;
  itemId: string;
  /** Names of this workspace's skills whose files are HERE (`filesHere`) —
   * the names an install would be refused for. */
  taken: ReadonlySet<string>;
  /** Called with the installed skill's name; the caller refreshes its list. */
  onInstalled: (name: string) => void;
  onBack: () => void;
  client?: Pick<SkillHubApi, "browse" | "install">;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setQ(query.trim()), 250);
    return () => clearTimeout(id);
  }, [query]);
  const listQ = useInfiniteQuery({
    queryKey: [...qk.skillHub(q, false, slug), "paged"],
    queryFn: ({ pageParam }) => client.browse({ q, app: slug, offset: pageParam, limit: PAGE }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.entries.length, 0);
      return loaded < last.total && last.entries.length > 0 ? loaded : undefined;
    },
    placeholderData: keepPreviousData,
  });
  const [failure, setFailure] = useState<string | null>(null);
  const install = useMutation({
    mutationFn: (entryId: string) => client.install(slug, itemId, entryId),
    onSuccess: (res) => onInstalled(res.name),
    onError: (e) => setFailure(describeRefusal(e, t)),
    // The refusal is shown right here (`failure`), so the query client's
    // global write-failure toast must not report it a second time
    // (plan-skill-hub-ui-polish D3).
    meta: { silentError: true },
  });
  const rows = (listQ.data?.pages ?? []).flatMap((p) => p.entries);
  const total = listQ.data?.pages[0]?.total ?? 0;

  return (
    <div className="skills-picker" data-testid="skill-hub-picker">
      <div className="skills-panel-head">
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="ghost"
          data-testid="skill-hub-picker-back"
          onClick={onBack}
        >
          <Icon name="chev_l" size={12} /> {t("skills.fromHub.back")}
        </button>
        <strong>{t("skills.fromHub")}</strong>
      </div>
      <p className="skills-panel-intro">{t("skills.fromHub.intro")}</p>
      <p className="skills-panel-intro">
        <Link to="/skill-hub">{t("skills.fromHub.browse")}</Link>
      </p>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t("skillHub.search")}
        aria-label={t("skillHub.search")}
        className="input input--block"
      />
      {failure ? (
        <p className="error" role="alert">
          {t("skillHub.failed", { reason: failure })}
        </p>
      ) : null}
      <div className="scrollable skills-panel-list">
        {listQ.isError ? (
          <p className="error" role="alert">
            {t("skillHub.error")}
          </p>
        ) : listQ.isPending ? (
          <p className="muted">{t("skillHub.loading")}</p>
        ) : rows.length === 0 ? (
          <p data-testid="skill-hub-picker-empty" className="muted">
            {q ? t("skillHub.noMatch.query", { q }) : t("skills.fromHub.none")}
          </p>
        ) : (
          <>
            <ul className="skills-picker-rows">
              {rows.map((row) => (
                <PickRow
                  key={row.id}
                  row={row}
                  taken={taken.has(row.name)}
                  busy={install.isPending}
                  onInstall={() => {
                    setFailure(null);
                    install.mutate(row.id);
                  }}
                />
              ))}
            </ul>
            {listQ.hasNextPage ? (
              <div className="skill-hub-more">
                <span className="muted small">
                  {t("skillHub.shown", { shown: rows.length, total })}
                </span>
                <button
                  type="button"
                  className="btn"
                  data-size="sm"
                  data-variant="secondary"
                  disabled={listQ.isFetchingNextPage}
                  onClick={() => void listQ.fetchNextPage()}
                >
                  {t("skillHub.loadMore")}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function PickRow({
  row,
  taken,
  busy,
  onInstall,
}: {
  row: SkillHubCard;
  taken: boolean;
  busy: boolean;
  onInstall: () => void;
}) {
  const t = useT();
  const owner = useUser(row.owner).name;
  return (
    <li data-testid={`pick-${row.id}`} className="skills-picker-row">
      <div className="skills-row-text">
        <div className="skills-row-line">
          <Link
            to={`/skill-hub/${encodeURIComponent(row.id)}`}
            className="skills-row-name"
            target="_blank"
            rel="noopener"
          >
            {row.name}
          </Link>
          <span className="skills-row-status">
            {owner}
            {row.forked_from
              ? ` ・ ${
                  row.origin
                    ? t("skillHub.forkOf", { origin: `${row.origin.owner}/${row.origin.name}` })
                    : t("skillHub.forkOf.gone")
                }`
              : ""}
          </span>
        </div>
        <div className="skills-row-desc" title={row.description}>
          {row.description}
        </div>
        {row.missing_tools.length > 0 ? (
          // The告知 (plan Q1): named, on the row, never a block.
          <div data-testid={`pick-missing-${row.id}`} className="skills-row-warn">
            {t("skills.fromHub.missing", { tools: row.missing_tools.join(", ") })}
          </div>
        ) : null}
      </div>
      <div data-testid={`pick-actions-${row.id}`} className="skills-row-controls">
        {taken ? (
          <span data-testid={`pick-taken-${row.id}`} className="skills-row-status">
            {t("skills.fromHub.taken")}
          </span>
        ) : null}
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="primary"
          data-testid={`pick-install-${row.id}`}
          disabled={busy || taken}
          onClick={onInstall}
        >
          {t("skills.fromHub.install")}
        </button>
      </div>
    </li>
  );
}
