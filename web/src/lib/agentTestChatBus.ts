/**
 * "Open a test chat with this agent" (`docs/plan-wui-publish-and-agent-hub.md` A6).
 *
 * The agent panel is in the editor area and the item's chat beside it; neither
 * is the other's ancestor, so the ask travels on a bus keyed by item — the
 * same shape as `chatRequestBus`. The chat shell opens a new chat that runs as
 * the agent (`agent_path` on the conversation) and puts it on screen.
 */

import { createScopedBus } from "./scopedBus";

export type AgentTestChat = { path: string; title: string };

const bus = createScopedBus<AgentTestChat>();

export const subscribeAgentTestChat = bus.subscribe;
export const publishAgentTestChat = bus.publish;
/** Whether the item's chat is mounted to answer. */
export const canOpenAgentTestChat = bus.has;
