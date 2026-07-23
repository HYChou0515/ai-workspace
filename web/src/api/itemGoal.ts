/**
 * #613 P3: one chat's goal — the panel's REST surface.
 *
 * PUT sets the completion condition (one goal per chat; replaces), DELETE
 * clears it, GET hydrates. `checker_enabled` discloses whether this deploy has
 * a checker LLM wired — false means a set goal will NOT auto-continue, and the
 * panel says so instead of failing silently. Live updates ride the chat stream
 * as `goal_updated` events.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

export type GoalState = "active" | "met" | "exhausted";
export type ChatGoal = {
  condition: string;
  set_by: string;
  rounds_used: number;
  state: GoalState;
  max_rounds: number;
};
export type GoalRead = { goal: ChatGoal | null; checker_enabled: boolean };

export type ItemGoalApi = {
  getGoal(slug: string, itemId: string, chatId: string): Promise<GoalRead>;
  putGoal(slug: string, itemId: string, chatId: string, condition: string): Promise<GoalRead>;
  deleteGoal(slug: string, itemId: string, chatId: string): Promise<void>;
};

const goalUrl = (slug: string, itemId: string, chatId: string) =>
  `/a/${enc(slug)}/items/${enc(itemId)}/chats/${enc(chatId)}/goal`;

export const itemGoalApi: ItemGoalApi = {
  async getGoal(slug, itemId, chatId) {
    const r = await apiFetch(goalUrl(slug, itemId, chatId));
    return (await r.json()) as GoalRead;
  },
  async putGoal(slug, itemId, chatId, condition) {
    const r = await apiFetch(goalUrl(slug, itemId, chatId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ condition }),
    });
    return (await r.json()) as GoalRead;
  },
  async deleteGoal(slug, itemId, chatId) {
    await apiFetch(goalUrl(slug, itemId, chatId), { method: "DELETE" });
  },
};
