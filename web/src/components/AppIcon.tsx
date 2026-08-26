/**
 * Renders an App's manifest `icon` in any of its three forms — the NAME of a
 * file the App ships (`icon.png`, `icon.svg`, …), an emoji grapheme, or a
 * named-icon key — at a consistent size (#89). Shared by the Launcher cards and
 * the workspace shell's brand mark, so an App's identity is data (app.json),
 * not hardcoded per surface.
 *
 * A file icon is FETCHED, never inlined: the manifest carries the file's name
 * and the picture comes from `GET /apps/{slug}/icon`. That is what lets an App
 * ship a PNG (a raster has no markup to fold into JSON), and it keeps every
 * surface drawing the same mark — the launcher list endpoint has no room to
 * inline anything, so an inlined icon was only ever visible on some screens.
 */

import { API_PREFIX } from "../api/http";

import { Icon, type IconName } from "./Icon";

// The extensions the backend serves from the icon route. Kept in step with
// `ICON_MEDIA_TYPES` in apps/manifest.py — a name this doesn't match falls
// through to the named-icon path, where an unknown key draws the fallback
// glyph rather than a broken image.
const FILE_ICON = /\.(png|svg|jpe?g|webp|gif)$/i;

export function AppIcon({
  icon,
  slug,
  color,
  size = 24,
}: {
  icon: string;
  /** The App the icon belongs to — the icon route is per-App. Absent (or an
   * icon that isn't a filename) keeps the emoji / named-icon paths. */
  slug?: string;
  color?: string;
  size?: number;
}) {
  if (slug && FILE_ICON.test(icon)) {
    return (
      <img
        src={`${API_PREFIX}/apps/${encodeURIComponent(slug)}/icon`}
        alt=""
        aria-hidden
        width={size}
        height={size}
        // The manifest states a name, not a shape: constrain the box and let the
        // image letterbox inside it, so a non-square file can't stretch the row.
        style={{ width: size, height: size, objectFit: "contain", display: "block" }}
      />
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
