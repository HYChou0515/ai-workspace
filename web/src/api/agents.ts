/**
 * The agent panel's summary (`docs/plan-wui-publish-and-agent-hub.md` A6) —
 * `GET /a/{slug}/items/{id}/agent?path=`, answered by the code a conversation
 * runs through (`api/agent_routes.py`).
 */

import { apiFetch, httpErrorFrom } from "./http";
import type { Published } from "./publish";

export type AgentSuggestion = { label: string; prompt: string };
export type AgentNote = { at: number; by: string; text: string };

export type AgentSummary = {
  path: string;
  /** False when the file does not define an agent yet; `problems` says why. */
  valid: boolean;
  problems: string[];
  title: string;
  description: string;
  icon: string;
  color: string;
  instructions: string;
  /** Whether its `tools:` narrows the App's tools at all. */
  narrows: boolean;
  /** What a conversation with it holds in this item. */
  tools: string[];
  /** Names its `tools:` lists that the App does not grant — not there. */
  not_available: string[];
  skills: string[];
  suggestions: AgentSuggestion[];
  published: Published | null;
  opens: number;
  people: number;
  /** The latest release notes, newest first (A9). */
  notes: AgentNote[];
};

const enc = encodeURIComponent;

export const agentApi = {
  async summary(slug: string, itemId: string, path: string): Promise<AgentSummary> {
    const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/agent?path=${enc(path)}`);
    if (!resp.ok) throw await httpErrorFrom(resp, `agent summary failed: ${resp.status}`);
    return (await resp.json()) as AgentSummary;
  },
};

export type AgentApi = typeof agentApi;
