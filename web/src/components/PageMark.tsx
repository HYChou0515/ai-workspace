/**
 * The mark at the left edge of a WUI overview row — one circle of one size per
 * row (`docs/plan-wui-overview-icon-favourites.md`).
 *
 * The page's view file may declare `icon:` in the three forms an App's
 * manifest icon takes (`AppIcon`): a file in the page's folder, one emoji, or
 * a named-icon key. The server stores the string at Deploy and validates
 * nothing — a decoration must not block a page from shipping — so resolving
 * it is this component's job, and every way it can fail lands on the same
 * default: the title's first letters. A file that does not load, a key nobody
 * knows, an empty string — all the circle with letters, which on the overview
 * is also how the author sees the icon did not take.
 *
 * Styled by the `.page` shell's `.page-mark` rules in `styles/my-resources.css`
 * — the App's tint and ink are published the way `AppTag` publishes them, so a
 * group's circles agree with its heading's pill, and the CSS picks the ink for
 * the theme (only it knows which is on).
 */
import { useState } from "react";

import { API_PREFIX } from "../api/http";
import { encodePath } from "../api/refPath";
import { useApps } from "../hooks/useResources";
import { appTagPalette, pageColour } from "../lib/appColor";
import { Icon, isIconName } from "./Icon";

// The same extensions `AppIcon` treats as a file — kept in step with it and
// with `ICON_MEDIA_TYPES` in apps/manifest.py. Anything else is text.
const FILE_ICON = /\.(png|svg|jpe?g|webp|gif)$/i;

/** The default: the title's first letters. CJK (anything not starting with a
 * latin letter) gives its first grapheme; a latin title gives the initials of
 * its first two words — `UserAvatar`'s split, so the two circles on the
 * platform read the same way. `"?"` for nothing, so the circle is never empty. */
export function markLetters(title: string): string {
  const t = title.trim();
  if (!t) return "?";
  if (/^[A-Za-z]/.test(t)) {
    return t
      .split(/[\s_-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase() ?? "")
      .join("");
  }
  return firstGrapheme(t);
}

function firstGrapheme(s: string): string {
  // `Intl.Segmenter` where the runtime has it (a ZWJ family emoji is one
  // grapheme); the first code point otherwise — the same answer for every
  // CJK title, which is what this is for.
  const Seg = (Intl as { Segmenter?: new (l?: string, o?: { granularity: string }) => { segment(s: string): Iterable<{ segment: string }> } }).Segmenter;
  if (Seg) {
    for (const g of new Seg(undefined, { granularity: "grapheme" }).segment(s)) return g.segment;
  }
  return [...s][0] ?? "?";
}

/** `icon`'s form, in `AppIcon`'s order: a file name, then one emoji (at most two
 * code points — a symbol plus its variation selector), then a named icon;
 * anything else is "none". */
function formOf(icon: string): "file" | "emoji" | "named" | "none" {
  if (!icon) return "none";
  if (FILE_ICON.test(icon)) return "file";
  if ([...icon].length <= 2 && !/^[a-z_]+$/.test(icon)) return "emoji";
  if (isIconName(icon)) return "named";
  return "none";
}

/** The page's folder — the view file's path without its last segment — so a
 * file icon is looked for beside the view file, the rule `entry:` follows. A
 * page at the workspace root has no folder and its icon sits at the root. */
function folderOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i <= 0 ? "" : path.slice(0, i);
}

export function PageMark({
  slug,
  itemId,
  path,
  icon,
  color,
  title,
  size = 28,
}: {
  slug: string;
  itemId: string;
  /** The view file's workspace path. */
  path: string;
  /** The row's `icon` as the server stored it — `""` for none. */
  icon: string;
  /** The row's `color` as the server stored it — the page's own, `""` for
   * none; the App's colour is the fallback (`pageColour`). */
  color?: string;
  title: string;
  size?: number;
}) {
  // A file that did not load is "none" for the rest of the row's life: the
  // browser has already said so, and retrying on every render would ask again.
  const [broken, setBroken] = useState(false);
  const apps = useApps();
  const palette = appTagPalette(pageColour(color, apps.find((a) => a.slug === slug)?.color));
  const form = broken ? "none" : formOf(icon);
  return (
    <span
      className="page-mark"
      data-testid="page-mark"
      aria-hidden="true"
      style={
        {
          width: size,
          height: size,
          fontSize: Math.round(size * 0.43),
          // Custom properties, hex — the same three `AppTag` publishes, read by
          // the sheet per theme. Never `oklch()` inline: happy-dom drops it.
          ...(palette
            ? {
                "--app-tint": palette.tint,
                "--app-ink": palette.inkLight,
                "--app-ink-dark": palette.inkDark,
              }
            : {}),
        } as React.CSSProperties
      }
    >
      {form === "file" ? (
        <img
          src={`${API_PREFIX}/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/files/${encodePath(`${folderOf(path)}/${icon}`)}`}
          alt=""
          onError={() => setBroken(true)}
        />
      ) : form === "emoji" ? (
        <span className="page-mark-emoji">{icon}</span>
      ) : form === "named" && isIconName(icon) ? (
        <Icon name={icon} size={Math.round(size * 0.57)} color="currentColor" />
      ) : (
        markLetters(title)
      )}
    </span>
  );
}
