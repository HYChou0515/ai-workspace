/**
 * The glyph that precedes a label in the platform menus — the global bar's
 * app switcher (`GlobalNav`) and the chat rail's ☰ (`ChatListRail`).
 *
 * Both menus list the same entries from the same data (`useApps`,
 * `usePlatformDestinations`), and that data was unified because the two had
 * drifted. The LOOK then drifted the same way: the switcher drew every entry's
 * icon and the rail drew text only. One component for the glyph is what keeps
 * that from happening again — the link around it stays each menu's own, since
 * their semantics differ (`aria-current` in a Popover vs `role="menuitem"` in
 * a `role="menu"`) and each has tests pinning them.
 *
 * Every glyph sits in the same 22px box, whatever it is: an App's manifest
 * icon at 22px (`AppIcon` — file, emoji or named), or a destination's named
 * icon at 16px. The box is what lines the labels up — an emoji's line box is
 * taller than 22px and an empty `icon` has no width at all, and without the
 * box those two rows sat out of column with the rest.
 */

import type { AppSummary } from "../api/types";

import { AppIcon } from "./AppIcon";
import { Icon, type IconName } from "./Icon";

export const NAV_GLYPH_SIZE = 22;

export function NavGlyph(props: { app: AppSummary } | { icon: IconName }) {
  return (
    <span
      style={{
        width: NAV_GLYPH_SIZE,
        height: NAV_GLYPH_SIZE,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        lineHeight: 1,
      }}
    >
      {"app" in props ? (
        // The manifest defaults `color` to ""; an empty stroke paints nothing,
        // where Icon's own default (`currentColor`) draws the glyph in the text
        // colour.
        <AppIcon
          icon={props.app.icon}
          slug={props.app.slug}
          color={props.app.color || undefined}
          size={NAV_GLYPH_SIZE}
        />
      ) : (
        <Icon name={props.icon} size={16} color="var(--text-paper-d)" />
      )}
    </span>
  );
}
