/**
 * The platform destinations behind a navigation menu — the list AND the
 * conditions that decide who sees each one.
 *
 * There are two menus: `GlobalNav`'s app switcher and, in a chat-first App, the
 * `ChatListRail`'s ☰. They were written independently — GlobalNav hand-wrote a
 * `<FixedLink>` per destination, the rail kept a `PLATFORM_LINKS` array — and
 * they had already drifted: the rail was missing **My resources** (everyone),
 * **Groups** (owners/members) and **Work calendar** (superusers), and it had no
 * gating with which to express the last two even if someone added them. In chat
 * mode a person simply could not reach their own resources.
 *
 * A shared constant array would not have been enough, because the drift is in
 * the CONDITIONS as much as the entries. So this is a hook: it resolves the
 * viewer's superuser / group state once and returns only what they may open.
 *
 * `/help` is included, and GlobalNav filters it out — it renders Help as its own
 * persistent "?" button elsewhere in the bar (#230), so listing it twice there
 * would be a duplicate. The rail has no such button, so it shows it in the menu.
 */
import { useQuery } from "@tanstack/react-query";

import type { IconName } from "../components/Icon";
import { groupsApi } from "../api/groups";
import { qk } from "../api/queryKeys";
import { useT } from "../lib/i18n";
import { useIsSuperuser } from "./useIsSuperuser";

export type PlatformDestination = {
  to: string;
  label: string;
  /** Icon name for surfaces that render one; the rail's menu is text-only. */
  icon: IconName;
};

export function usePlatformDestinations(): PlatformDestination[] {
  const t = useT();
  // #608: Groups is for superusers (who create them) and anyone who belongs to
  // one. Cached app-wide; degrades to hidden if it cannot load.
  const isSuperuser = useIsSuperuser();
  const { data: myGroups = [] } = useQuery({
    queryKey: qk.groups,
    queryFn: () => groupsApi.listGroups(),
  });
  const showGroups = isSuperuser || myGroups.length > 0;

  return [
    { to: "/kb", label: "Knowledge base", icon: "layers" },
    { to: "/review", label: t("review.title"), icon: "check" },
    { to: "/diagnostics", label: "Diagnostics", icon: "sparkle" },
    { to: "/my-resources", label: t("resources.title"), icon: "layers" },
    ...(showGroups
      ? ([{ to: "/groups", label: "Groups", icon: "users" }] satisfies PlatformDestination[])
      : []),
    ...(isSuperuser
      ? ([
          { to: "/work-calendar", label: "Work calendar", icon: "clock" },
        ] satisfies PlatformDestination[])
      : []),
    { to: "/help", label: "Help", icon: "quote" },
  ];
}
