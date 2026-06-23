/**
 * A decorative shimmer placeholder shown in place of content that is still
 * loading (issue #151). Purely visual: `aria-hidden` so assistive tech skips
 * it (the loading region announces itself via `aria-busy`, not the blocks).
 *
 * Callers size each placeholder with `className` / `style`; this primitive only
 * owns the shimmer.
 */
import type { CSSProperties } from "react";

export function Skeleton({
  className,
  style,
}: {
  className?: string;
  style?: CSSProperties;
}) {
  return <div className={`skeleton${className ? ` ${className}` : ""}`} style={style} aria-hidden="true" />;
}
