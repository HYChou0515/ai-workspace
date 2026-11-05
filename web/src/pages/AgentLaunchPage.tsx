/**
 * Going to an agent — `/agents/{id}/go` (`?new=1` for a new conversation).
 *
 * The user's decision (2026-11-05, overturning A8's "land on the agent's page
 * first" and A11's "no New chat"): clicking an agent goes straight into a
 * conversation with it — the person's latest one, or a first one made on the
 * spot — and a new conversation and the history are a press away from
 * inside. NN/g: *interaction cost* (people come to talk, not to choose
 * first), *empty states* (never a page that only says there is nothing yet),
 * and *progressive disclosure* (the history when they want it). The agent's
 * page stays the ONE place for its introduction and history together.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { type AgentHubApi, agentHubApi } from "../api/agentHub";
import { HttpError } from "../api/http";
import { useT } from "../lib/i18n";
import { agentOpenRoute } from "./AgentHubPage";

export function AgentLaunchPage({ client = agentHubApi }: { client?: Pick<AgentHubApi, "page"> }) {
  const t = useT();
  const { agentId = "" } = useParams();
  const [params] = useSearchParams();
  const fresh = params.get("new") === "1";
  const navigate = useNavigate();
  // Read afresh for THIS visit: which conversation is the latest changes as
  // the person starts new ones.
  const visit = useLocation().key;
  const page = useQuery({
    queryKey: ["agentLaunch", agentId, visit],
    queryFn: () => client.page(agentId),
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  const data = page.data;
  useEffect(() => {
    if (!data) return;
    // `mine` is newest first: the latest is the one they last started.
    const latest = fresh ? undefined : data.mine[0];
    navigate(
      latest
        ? `/a/${encodeURIComponent(latest.slug)}/${encodeURIComponent(latest.item_id)}`
        : agentOpenRoute(data.agent),
      { replace: true },
    );
  }, [data, fresh, navigate]);

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
  return (
    <p className="page" role="status">
      {t("agentHub.going")}
    </p>
  );
}
