/**
 * Which App a row belongs to, as a pill — shared by every platform page that
 * lists things across apps (`/my-resources`, `/wui`). Styled by the `.page`
 * shell's `.app-tag` rules in `styles/my-resources.css`, so it renders inside
 * a `.page` root and nowhere else.
 */

import { AppIcon } from "./AppIcon";
import { useApps } from "../hooks/useResources";
import { appTagPalette } from "../lib/appColor";

/** Which App a row belongs to, as a pill.
 *
 * ONE component for both lists. They answer the same question about the same
 * items — which of my things is this? — and naming the App only on the live
 * half left a reader able to tell a pm workspace from an rca one while deciding
 * what to CLOSE and unable to while deciding what to DELETE, which is the
 * irreversible half.
 *
 * The name falls back to the slug, and that is not cosmetic: `listApps` is a
 * separate near-static query, so it is EMPTY on the first paint and STAYS empty
 * if that request fails — rendering nothing would flicker on every load and, on
 * a failure, leave every row looking like it belongs to no App at all. The slug
 * is the honest answer in that window; it is what the row's link already uses.
 * Never a slug→name table in the FE — the name is the manifest's to state.
 *
 * Nothing at all (an empty cell, so the rows below still line up) when the
 * backend could not name the row: `/me/resources` degrades an item it cannot
 * resolve to empty strings rather than dropping it (it is running and being
 * charged for, so it must stay closable), and a pill is a fill plus padding —
 * an empty one is a grey smudge in the column where every other row speaks. */
export function AppTag({ slug }: { slug: string }) {
  const apps = useApps();
  const app = apps.find((a) => a.slug === slug);
  const name = app?.title || slug;
  if (!name) return <span />;
  // The App's own mark and its own colour. `AppIcon` because an App may ship a
  // PNG, an emoji or a named icon and only it knows the difference — a pill that
  // handled one of the three would look right on this deploy and blank on the
  // next. Both are absent until `listApps` resolves, which is why the pill
  // renders from `name` alone and dresses itself when the manifest arrives.
  const palette = appTagPalette(app?.color);
  return (
    <span
      className="app-tag"
      // Published as custom properties rather than as `color`/`background`
      // directly: the ink has to differ per theme, and only CSS knows which
      // theme is on. Plain rgba/hex values, never `oklch()` — happy-dom drops
      // that from an inline style, which would blind every colour guard.
      style={
        palette
          ? ({
              "--app-tint": palette.tint,
              "--app-ink": palette.inkLight,
              "--app-ink-dark": palette.inkDark,
            } as React.CSSProperties)
          : undefined
      }
    >
      {app?.icon ? <AppIcon icon={app.icon} slug={app.slug} color="currentColor" size={13} /> : null}
      {/* The label owns its own overflow. `text-overflow` applies to a block
          container, and bare text inside the `inline-flex` pill becomes an
          anonymous flex item that never inherits it — so a name too long for
          the column was hard-clipped mid-word with no ellipsis at all. `title`
          because an ellipsis says a name was cut and not what it was. */}
      <span className="app-tag-label" title={name}>
        {name}
      </span>
    </span>
  );
}
