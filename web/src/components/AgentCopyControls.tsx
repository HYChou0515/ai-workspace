/**
 * The controls of a conversation with an agent (A11, as the user amended it
 * 2026-11-05): the way back to Agent Hub, a new conversation, and the history.
 *
 * They live IN the workspace's own top bar, not in a bar of their own above it
 * (the user, 2026-11-05: 「兩個 header 不行」). Two stacked headers both naming
 * the agent spend the height twice and leave no single place that says where
 * you are — NN/g, *Aesthetic and minimalist design* (no redundant chrome) and
 * one page header per page.
 */

import { Link } from "react-router-dom";

import type { AppItem } from "../api/types";
import { useT } from "../lib/i18n";
import { agentConversationHref, agentPageHref } from "../pages/AgentHubPage";

/** Whether ``item`` is a conversation copy of a published agent. */
export function isAgentCopy(item: AppItem): boolean {
  return item.published_kind === "agent";
}

/** Before the title: the way back to Agent Hub. */
export function AgentCopyBack() {
  const t = useT();
  return (
    <Link to="/agents" style={{ whiteSpace: "nowrap" }}>
      ← {t("agentHub.back")}
    </Link>
  );
}

/** After the title: a new conversation (a new copy) and the history — the
 * agent's page, its introduction and the person's conversations together.
 * Links: both go somewhere; a new conversation is made where it lands. */
export function AgentCopyActions({ item }: { item: AppItem }) {
  const t = useT();
  const from = String(item.published_from ?? "");
  if (!from) return null;
  return (
    <span style={{ display: "flex", gap: 8, flexShrink: 0 }}>
      <Link className="btn" data-variant="secondary" data-size="sm" to={agentConversationHref(from, true)}>
        {t("agentHub.newConversation")}
      </Link>
      <Link className="btn" data-variant="ghost" data-size="sm" to={agentPageHref(from)}>
        {t("agentHub.history")}
      </Link>
    </span>
  );
}
