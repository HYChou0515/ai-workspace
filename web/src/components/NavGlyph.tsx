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
 * An App's glyph is its manifest icon at 22px (`AppIcon` — file, emoji or
 * named). A destination's is a named icon at 16px inside a 22px box, so the
 * labels line up with the App rows above them.
 */

import type { AppSummary } from "../api/types";

import { AppIcon } from "./AppIcon";
import { Icon, type IconName } from "./Icon";

export const NAV_GLYPH_SIZE = 22;

export function NavGlyph(props: { app: AppSummary } | { icon: IconName }) {
  if ("app" in props) {
    const { icon, slug, color } = props.app;
    return <AppIcon icon={icon} slug={slug} color={color} size={NAV_GLYPH_SIZE} />;
  }
  return (
    <span
      style={{
        width: NAV_GLYPH_SIZE,
        height: NAV_GLYPH_SIZE,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      <Icon name={props.icon} size={16} color="var(--text-paper-d)" />
    </span>
  );
}
