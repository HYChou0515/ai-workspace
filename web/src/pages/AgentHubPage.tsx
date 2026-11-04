/**
 * Agent Hub (`docs/plan-wui-publish-and-agent-hub.md` A1/A7) — its own place in
 * the platform menu, beside WUI.
 *
 * Every published agent the viewer may use, laid out like the `/wui` overview
 * (cards with an icon, a list mode, favourites — the same sheet, `.page
 * .wui-cards`) because people already know how to read that page (NN/g,
 * *Consistency and standards*), under its own name and in its own place,
 * because an agent is a different thing to them than a page (A1). A card
 * opens the agent's page, where a conversation is started (A8); usage is
 * shown to everyone without names, like skill hub's.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { type AgentCard, agentHubApi, type AgentHubApi } from "../api/agentHub";
import { qk } from "../api/queryKeys";
import { exactTime, relativeTime } from "../api/types";
import { AppTag } from "../components/AppTag";
import { Icon } from "../components/Icon";
import { PageMark } from "../components/PageMark";
import { UserChip } from "../components/UserChip";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useCurrentUserState } from "../hooks/useCurrentUser";
import { useApps } from "../hooks/useResources";
import { appTagPalette, pageColour } from "../lib/appColor";
import { useT } from "../lib/i18n";
import { AGENT_HUB_FAVOURITES, favouriteKey, useWuiFavourites } from "../lib/wuiFavourites";
import { AGENT_HUB_VIEW, useWuiView } from "../lib/wuiView";
import { ViewSwitch } from "./WuiOverviewPage";

export const AGENT_HUB = "Agent Hub";

/** An agent's page in Agent Hub (A8). */
export function agentPageHref(id: string): string {
  return `/agents/${encodeURIComponent(id)}`;
}

export function AgentHubPage({ client = agentHubApi }: { client?: Pick<AgentHubApi, "list"> }) {
  const t = useT();
  // "Agent Hub" is the proper noun the menu uses — always in full (A1).
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: AGENT_HUB }]);
  const me = useCurrentUserState();
  const favourites = useWuiFavourites(me.id, AGENT_HUB_FAVOURITES);
  const [view, setView] = useWuiView(AGENT_HUB_VIEW);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"newest" | "title">("newest");
  const { data, isPending, isError, refetch } = useQuery({ queryKey: qk.agentHub, queryFn: () => client.list() });

  if (isError) {
    return (
      <div className="page">
        <h1>{AGENT_HUB}</h1>
        <p className="error" role="alert">
          {t("agentHub.error")}{" "}
          <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={() => void refetch()}>
            {t("wui.retry")}
          </button>
        </p>
      </div>
    );
  }
  if (isPending) return <p>{t("agentHub.loading")}</p>;

  const needle = query.trim().toLocaleLowerCase();
  const shown = data
    .filter(
      (a) =>
        !needle ||
        a.title.toLocaleLowerCase().includes(needle) ||
        a.description.toLocaleLowerCase().includes(needle),
    )
    .sort((a, b) => (sort === "title" ? a.title.localeCompare(b.title, "zh-TW") : 0));
  const starred = shown.filter((a) => favourites.has(favouriteKey(a)));
  const listOf = (agents: AgentCard[], showApp: boolean) => {
    const props = (a: AgentCard) => ({
      agent: a,
      starred: favourites.has(favouriteKey(a)),
      starReady: me.ready,
      onStar: () => favourites.toggle(favouriteKey(a)),
      showApp,
    });
    return view === "cards" ? (
      <ul className="wui-cards">
        {agents.map((a) => (
          <AgentCardItem key={a.id} {...props(a)} />
        ))}
      </ul>
    ) : (
      <ul className="wui-list">
        {agents.map((a) => (
          <AgentRow key={a.id} {...props(a)} />
        ))}
      </ul>
    );
  };
  const nothing = data.length === 0;

  return (
    <div className={view === "cards" && !nothing ? "page page--wide" : "page"}>
      <div className="page-head">
        <h1>{AGENT_HUB}</h1>
        {nothing ? null : <ViewSwitch view={view} onChange={setView} />}
      </div>
      {nothing ? (
        <>
          <p className="empty">{t("agentHub.empty")}</p>
          <p className="hint">{t("agentHub.empty.what")}</p>
        </>
      ) : (
        <>
          <div className="page-tools">
            <input
              autoComplete="off"
              className="input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("agentHub.search")}
              aria-label={t("agentHub.search")}
            />
            <label>
              {t("wui.sort")}
              <select
                className="input"
                value={sort}
                onChange={(e) => setSort(e.target.value === "title" ? "title" : "newest")}
                aria-label={t("wui.sort")}
              >
                <option value="newest">{t("agentHub.sort.newest")}</option>
                <option value="title">{t("wui.sort.title")}</option>
              </select>
            </label>
          </div>
          {shown.length === 0 ? <p className="empty">{t("agentHub.nomatch")}</p> : null}
          {starred.length > 0 ? (
            <section aria-labelledby="agent-hub-favourites">
              <h2 id="agent-hub-favourites">{t("wui.favourites")}</h2>
              {listOf(starred, true)}
            </section>
          ) : null}
          {shown.length > 0 ? (
            <section aria-labelledby="agent-hub-all">
              <h2 id="agent-hub-all">{t("agentHub.all")}</h2>
              {listOf(shown, true)}
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}

type ItemProps = {
  agent: AgentCard;
  starred: boolean;
  starReady: boolean;
  onStar: () => void;
  showApp: boolean;
};

function Star({ agent, starred, starReady, onStar, className = "btn" }: ItemProps & { className?: string }) {
  const t = useT();
  return (
    <button
      type="button"
      className={className}
      data-variant="ghost"
      data-size="sm"
      aria-pressed={starred}
      aria-label={starred ? t("wui.unstar", { title: agent.title }) : t("wui.star", { title: agent.title })}
      disabled={!starReady}
      onClick={onStar}
    >
      <Icon name="star" size={16} />
    </button>
  );
}

function Usage({ agent }: { agent: AgentCard }) {
  const t = useT();
  const iso = new Date(agent.published_at).toISOString();
  return (
    <span className="detail" title={exactTime(iso)} data-testid="agent-usage">
      {t("publish.usage", { opens: agent.opens, people: agent.people })} · {relativeTime(iso)}
    </span>
  );
}

function AgentRow(props: ItemProps) {
  const { agent, showApp } = props;
  return (
    <li>
      <PageMark
        slug={agent.slug}
        itemId={agent.item_id}
        path={agent.path}
        icon={agent.icon}
        color={agent.color}
        title={agent.title}
      />
      <Link to={agentPageHref(agent.id)} title={agent.title}>
        {agent.title}
      </Link>
      <span className="detail">
        {showApp ? <AppTag slug={agent.slug} /> : null} {agent.description}
      </span>
      <Usage agent={agent} />
      <Star {...props} />
    </li>
  );
}

function AgentCardItem(props: ItemProps) {
  const { agent, showApp } = props;
  const app = useApps().find((a) => a.slug === agent.slug);
  const colour = pageColour(agent.color, app?.color);
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
          slug={agent.slug}
          itemId={agent.item_id}
          path={agent.path}
          icon={agent.icon}
          color={agent.color}
          title={agent.title}
          size={54}
        />
        <div className="wui-card-text">
          {/* Stretched over the card by the sheet, like a page card's link —
              here into Agent Hub, not a new tab: the agent's page is where a
              conversation is started. */}
          <Link to={agentPageHref(agent.id)} title={agent.title}>
            {agent.title}
          </Link>
          <span className="detail" title={agent.description}>
            {agent.description}
          </span>
        </div>
      </div>
      <Star {...props} className="btn star" />
      <div className="wui-card-foot">
        <span className="wui-card-who">
          {showApp ? <AppTag slug={agent.slug} /> : null}
          <UserChip userId={agent.published_by} size={18} />
        </span>
        <Usage agent={agent} />
      </div>
    </li>
  );
}
