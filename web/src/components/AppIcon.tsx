/**
 * Renders an App's manifest `icon` in any of its three forms — inline `<svg>`
 * markup, an emoji grapheme, or a named-icon key — at a consistent size (#89).
 * Shared by the Launcher cards and the workspace shell's brand mark, so an App's
 * identity is data (app.json), not hardcoded per surface.
 */

import { Icon, type IconName } from "./Icon";

export function AppIcon({
  icon,
  color,
  size = 24,
}: {
  icon: string;
  color?: string;
  size?: number;
}) {
  if (icon.includes("<svg")) {
    return (
      <span aria-hidden style={{ display: "inline-flex" }} dangerouslySetInnerHTML={{ __html: icon }} />
    );
  }
  // A single grapheme that isn't a known icon name → treat as emoji.
  if (icon.length <= 2 && !/^[a-z_]+$/.test(icon)) {
    return (
      <span aria-hidden style={{ fontSize: size }}>
        {icon}
      </span>
    );
  }
  return <Icon name={icon as IconName} size={size} color={color} />;
}
