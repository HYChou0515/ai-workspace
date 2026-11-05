/**
 * One agent's page in Agent Hub (`docs/plan-wui-publish-and-agent-hub.md` A8/A9).
 *
 * What it is (description, who published it and when, usage), **Start a new
 * conversation** on top — every conversation is a new copy from the latest
 * version (A2, C7) — its latest release notes, and below, the viewer's own
 * conversations with it, each with its size, and a bulk delete (C8), so a
 * person can see where their space went and clear what they are done with.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { type AgentHubApi, agentHubApi, type MyConversation } from "../api/agentHub";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import { exactTime, relativeTime } from "../api/types";
import { useDialog } from "../components/Dialog";
import { PageMark } from "../components/PageMark";
import { UserChip } from "../components/UserChip";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";
import { AGENT_HUB, agentOpenRoute } from "./AgentHubPage";

export function AgentHubAgentPage({
  client = agentHubApi,
  removeItem = (slug: string, itemId: string) => api.deleteAppItem(slug, itemId),
}: {
  client?: Pick<AgentHubApi, "page">;
  removeItem?: (slug: string, itemId: string) => Promise<unknown>;
}) {
  const t = useT();
  const { agentId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const page = useQuery({ queryKey: qk.agentHubPage(agentId), queryFn: () => client.page(agentId) });
  useBreadcrumbs([
    { label: t("nav.home"), to: "/" },
    { label: AGENT_HUB, to: "/agents" },
    { label: page.data?.agent.title ?? "…" },
  ]);
  const remove = useMutation({
    mutationFn: async (items: MyConversation[]) => {
      for (const c of items) await removeItem(c.slug, c.item_id);
    },
    onSettled: () => {
      setPicked(new Set());
      void qc.invalidateQueries({ queryKey: qk.agentHubPage(agentId) });
    },
    meta: { silentError: true },
  });

  if (page.isPending) return <p>{t("agentHub.loading")}</p>;
  if (page.isError) {
    const gone = page.error instanceof HttpError && page.error.status === 404;
    return (
      <div className="page">
        <p className="error" role="alert">
          {t(gone ? "agentHub.notFound" : "agentHub.error")}
        </p>
        <Link to="/agents">{t("agentHub.back")}</Link>
      </div>
    );
  }
  const { agent, notes, mine } = page.data;
  const iso = new Date(agent.published_at).toISOString();
  // The preparing page makes the conversation and lands on it (C12).
  const start = () => navigate(agentOpenRoute(agent));
  const toggle = (id: string) =>
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const askRemove = () => {
    const items = mine.filter((c) => picked.has(c.item_id));
    void confirm({
      title: t("agentHub.mine.delete.title", { n: items.length }),
      body: t("agentHub.mine.delete.body"),
      actions: [
        { id: "cancel", label: t("publish.cancel") },
        { id: "go", label: t("agentHub.mine.delete"), variant: "danger" },
      ],
    }).then((choice) => {
      if (choice === "go") remove.mutate(items);
    });
  };

  return (
    <div className="page" data-testid="agent-hub-agent">
      <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
        <PageMark
          slug={agent.slug}
          itemId={agent.item_id}
          path={agent.path}
          icon={agent.icon}
          color={agent.color}
          title={agent.title}
          size={54}
        />
        <div style={{ minWidth: 0 }}>
          <h1 style={{ margin: 0 }}>{agent.title}</h1>
          {agent.description && <p style={{ margin: "4px 0 0" }}>{agent.description}</p>}
          <p className="hint" style={{ margin: "4px 0 0" }}>
            <UserChip userId={agent.published_by} size={18} /> · <span title={exactTime(iso)}>{relativeTime(iso)}</span>{" "}
            · {t("publish.usage", { opens: agent.opens, people: agent.people })}
          </p>
        </div>
      </div>

      <p style={{ margin: "18px 0" }}>
        <button type="button" className="btn" data-variant="primary" onClick={start}>
          {t("agentHub.start")}
        </button>
      </p>

      {notes.length > 0 && (
        <section aria-labelledby="agent-notes">
          <h2 id="agent-notes">{t("agentPanel.notes")}</h2>
          <ul className="wui-list">
            {notes.map((n) => (
              <li key={`${n.at}-${n.text}`}>
                <span title={exactTime(new Date(n.at).toISOString())}>{relativeTime(new Date(n.at).toISOString())}</span>
                <UserChip userId={n.by} size={18} />
                <span>{n.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="agent-mine">
        <h2 id="agent-mine">{t("agentHub.mine")}</h2>
        {mine.length === 0 ? (
          <p className="empty">{t("agentHub.mine.none")}</p>
        ) : (
          <>
            <ul className="wui-list">
              {mine.map((c) => (
                <li key={c.item_id}>
                  <input
                    type="checkbox"
                    checked={picked.has(c.item_id)}
                    onChange={() => toggle(c.item_id)}
                    aria-label={t("agentHub.mine.pick", { name: c.hint || t("agentHub.mine.unnamed") })}
                  />
                  <Link to={`/a/${encodeURIComponent(c.slug)}/${encodeURIComponent(c.item_id)}`}>
                    {c.hint || t("agentHub.mine.unnamed")}
                  </Link>
                  <span className="detail" title={exactTime(new Date(c.created_at).toISOString())}>
                    {relativeTime(new Date(c.created_at).toISOString())} · {formatBytes(c.bytes)}
                  </span>
                </li>
              ))}
            </ul>
            <p>
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                disabled={picked.size === 0 || remove.isPending}
                onClick={askRemove}
              >
                {t("agentHub.mine.delete")}
              </button>
            </p>
            {remove.isError && (
              <p className="error" role="alert">
                {t("agentHub.mine.delete.failed")}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
