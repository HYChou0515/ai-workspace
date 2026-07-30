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

/** `stalled` (#615) = the self-destruct gate stopped an unattended run that
 * was not moving — distinct from `exhausted`, which means it worked through
 * its whole budget. The two need different words: one wants a person, the
 * other wants a bigger budget. */
export type GoalState = "active" | "met" | "exhausted" | "stalled";
export type ChatGoal = {
  condition: string;
  set_by: string;
  rounds_used: number;
  state: GoalState;
  max_rounds: number;
  /** #615: may this goal keep working outside office hours? */
  offhours: boolean;
  /** #615: after-hours turns spent — counted separately from `rounds_used`,
   * and across ALL nights rather than renewed each evening. */
  offhours_rounds_used: number;
  offhours_max_rounds: number;
};
export type GoalRead = {
  goal: ChatGoal | null;
  checker_enabled: boolean;
  /** #615: whether this deploy has an after-hours window at all. False ⇒ the
   * opt-in renders as unavailable, never as a box that ticks but does nothing. */
  offhours_enabled: boolean;
};

export type ItemGoalApi = {
  getGoal(slug: string, itemId: string, chatId: string): Promise<GoalRead>;
  putGoal(
    slug: string,
    itemId: string,
    chatId: string,
    condition: string,
    offhours?: boolean,
  ): Promise<GoalRead>;
  deleteGoal(slug: string, itemId: string, chatId: string): Promise<void>;
};

const goalUrl = (slug: string, itemId: string, chatId: string) =>
  `/a/${enc(slug)}/items/${enc(itemId)}/chats/${enc(chatId)}/goal`;

export const itemGoalApi: ItemGoalApi = {
  async getGoal(slug, itemId, chatId) {
    const r = await apiFetch(goalUrl(slug, itemId, chatId));
    return (await r.json()) as GoalRead;
  },
  async putGoal(slug, itemId, chatId, condition, offhours = false) {
    const r = await apiFetch(goalUrl(slug, itemId, chatId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ condition, offhours }),
    });
    return (await r.json()) as GoalRead;
  },
  async deleteGoal(slug, itemId, chatId) {
    await apiFetch(goalUrl(slug, itemId, chatId), { method: "DELETE" });
  },
};
