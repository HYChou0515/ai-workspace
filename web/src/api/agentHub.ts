/**
 * Agent Hub (`docs/plan-wui-publish-and-agent-hub.md` A1/A7/A8) — the
 * published agents the viewer may use, and one agent's page with the viewer's
 * own conversations (`api/agent_hub_routes.py`).
 */

import type { AgentNote } from "./agents";
import { apiFetch, httpErrorFrom } from "./http";

export type AgentCard = {
  id: string;
  slug: string;
  item_id: string;
  path: string;
  title: string;
  description: string;
  icon: string;
  color: string;
  published_by: string;
  published_at: number;
  opens: number;
  /** Distinct people — never who (A7). */
  people: number;
  can_remove: boolean;
};

export type MyConversation = {
  slug: string;
  item_id: string;
  /** The first thing the viewer asked, cut — "" before anything was asked. */
  hint: string;
  created_at: number;
  /** What it holds on the viewer's quota (C8). */
  bytes: number;
};

export type AgentPageData = { agent: AgentCard; notes: AgentNote[]; mine: MyConversation[] };

export const agentHubApi = {
  async list(): Promise<AgentCard[]> {
    const resp = await apiFetch("/agents");
    if (!resp.ok) throw await httpErrorFrom(resp, `agent hub failed: ${resp.status}`);
    return ((await resp.json()) as { agents: AgentCard[] }).agents;
  },
  async page(id: string): Promise<AgentPageData> {
    const resp = await apiFetch(`/agents/${encodeURIComponent(id)}`);
    if (!resp.ok) throw await httpErrorFrom(resp, `agent failed: ${resp.status}`);
    return (await resp.json()) as AgentPageData;
  },
};

export type AgentHubApi = typeof agentHubApi;
