import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient, ItemSkillState } from "../api/types";
import { onRemindersChanged, reminderOf } from "../lib/skillUpdateReminders";
import { useCurrentUserState } from "./useCurrentUser";

/**
 * How many of this workspace's skill hub copies have a new version this person
 * has neither put off nor skipped — the mark on the workspace's skills button
 * (docs/plan-skill-hub-release-notes D1). The same query the Skills panel
 * reads, so opening it costs nothing more; 0 until the person is known (#997),
 * and re-counted the moment a Later or a Skip is pressed.
 */
export function useSkillUpdateMark(
  slug: string,
  itemId: string,
  client: Pick<ApiClient, "getItemSkills"> = api,
): number {
  return useDueSkillUpdates(slug, itemId, client).length;
}

/** The skill hub copies whose newest version this person still has to answer
 * — what the mark counts and the banner at the top of the item names. Empty until
 * the person is known (#997). */
export function useDueSkillUpdates(
  slug: string,
  itemId: string,
  client: Pick<ApiClient, "getItemSkills"> = api,
): ItemSkillState[] {
  const { id: me, ready } = useCurrentUserState();
  const skills = useQuery({
    queryKey: qk.itemSkills(slug, itemId),
    queryFn: () => client.getItemSkills(slug, itemId),
    enabled: !!slug && !!itemId,
  });
  const [, setTick] = useState(0);
  useEffect(() => onRemindersChanged(() => setTick((n) => n + 1)), []);
  if (!ready) return [];
  const now = Date.now();
  return (skills.data ?? []).filter((s) => {
    const latest = s.updates?.[0];
    return !!latest && !!s.hub_entry && reminderOf(me, s.hub_entry, latest.version, now).due;
  });
}
