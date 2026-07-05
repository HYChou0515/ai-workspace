/**
 * Minimal icon set sourced from design_handoff_rca_3.0/rca/system.jsx (`I`
 * component). Stroke 1.6px, round caps/joins — matches the design's
 * visual weight. Add new icons as the UI needs them.
 */

/** The registered icon keys, as a runtime tuple so callers holding an arbitrary
 * string (e.g. an App manifest's `icon`) can test membership before rendering. */
export const ICON_NAMES = [
  "search", "plus", "minus", "x", "chev_d", "chev_r", "chev_l", "folder", "file", "chat",
  "play", "term", "user", "users", "settings", "bell", "branch", "sparkle", "arrow_r", "arrow_u",
  "arrow_d", "git", "dots_h", "dots_v", "eye", "pin", "clock", "check", "split", "layers",
  "download", "upload", "filter", "tag", "bug", "flame", "refresh", "undo", "quote", "external",
  "paperclip", "pencil", "home", "kanban",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

/** True when `x` is a registered icon key. Lets a caller route an unknown key to
 * a fallback instead of `Icon` drawing (and now back-filling) a hollow svg (#456). */
export function isIconName(x: string): x is IconName {
  return (ICON_NAMES as readonly string[]).includes(x);
}

export function Icon({
  name,
  size = 16,
  color = "currentColor",
  strokeWidth = 1.6,
  style,
}: {
  name: IconName;
  size?: number;
  color?: string;
  strokeWidth?: number;
  style?: React.CSSProperties;
}) {
  const sp = {
    fill: "none",
    stroke: color,
    strokeWidth,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };

  const paths: Record<IconName, React.ReactNode> = {
    search: (
      <>
        <circle cx="10" cy="10" r="6" {...sp} />
        <path d="M14.5 14.5 L20 20" {...sp} />
      </>
    ),
    plus: <path d="M12 4 V20 M4 12 H20" {...sp} />,
    minus: <path d="M4 12 H20" {...sp} />,
    x: <path d="M5 5 L19 19 M5 19 L19 5" {...sp} />,
    chev_d: <path d="M6 9 L12 15 L18 9" {...sp} />,
    chev_r: <path d="M9 6 L15 12 L9 18" {...sp} />,
    chev_l: <path d="M15 6 L9 12 L15 18" {...sp} />,
    folder: (
      <path
        d="M3 7 V18 A1 1 0 0 0 4 19 H20 A1 1 0 0 0 21 18 V9 A1 1 0 0 0 20 8 H11 L9 5 H4 A1 1 0 0 0 3 6 Z"
        {...sp}
      />
    ),
    file: (
      <>
        <path d="M6 3 H14 L18 7 V20 A1 1 0 0 1 17 21 H7 A1 1 0 0 1 6 20 Z" {...sp} />
        <path d="M14 3 V7 H18" {...sp} />
      </>
    ),
    chat: <path d="M4 5 H20 V15 H10 L5 19 V15 H4 Z" {...sp} />,
    play: <path d="M7 5 L19 12 L7 19 Z" {...sp} />,
    term: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="1.5" {...sp} />
        <path d="M6 9 L9 12 L6 15 M11 15 H17" {...sp} />
      </>
    ),
    user: (
      <>
        <circle cx="12" cy="8" r="3.5" {...sp} />
        <path d="M5 20 Q5 14 12 14 Q19 14 19 20" {...sp} />
      </>
    ),
    users: (
      <>
        <circle cx="9" cy="9" r="3" {...sp} />
        <circle cx="17" cy="10" r="2.5" {...sp} />
        <path d="M3 19 Q3 14 9 14 Q15 14 15 19 M15 19 Q21 19 21 15 Q21 12 17 12" {...sp} />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="2.5" {...sp} />
        <path
          d="M12 3 L13 5 L15 4 L15 6 L17 6 L16 8 L18 9 L17 11 L19 12 L17 13 L18 15 L16 16 L17 18 L15 18 L15 20 L13 19 L12 21 L11 19 L9 20 L9 18 L7 18 L8 16 L6 15 L7 13 L5 12 L7 11 L6 9 L8 8 L7 6 L9 6 L9 4 L11 5 Z"
          {...sp}
        />
      </>
    ),
    bell: (
      <>
        <path d="M6 16 V11 A6 6 0 0 1 18 11 V16 L20 18 H4 Z" {...sp} />
        <path d="M10 21 H14" {...sp} />
      </>
    ),
    branch: (
      <>
        <circle cx="6" cy="6" r="2" {...sp} />
        <circle cx="6" cy="18" r="2" {...sp} />
        <circle cx="18" cy="9" r="2" {...sp} />
        <path d="M6 8 V16 M6 9 Q6 12 12 12 Q18 12 18 11" {...sp} />
      </>
    ),
    sparkle: (
      <>
        <path d="M12 3 L13 9 L19 10 L13 11 L12 17 L11 11 L5 10 L11 9 Z" {...sp} />
        <path d="M19 4 L20 6 L22 7 L20 8 L19 10 L18 8 L16 7 L18 6 Z" {...sp} />
      </>
    ),
    arrow_r: <path d="M5 12 H19 M14 7 L19 12 L14 17" {...sp} />,
    arrow_u: <path d="M12 19 V5 M7 10 L12 5 L17 10" {...sp} />,
    arrow_d: <path d="M12 5 V19 M7 14 L12 19 L17 14" {...sp} />,
    git: (
      <>
        <circle cx="6" cy="6" r="2" {...sp} />
        <circle cx="6" cy="18" r="2" {...sp} />
        <circle cx="18" cy="12" r="2" {...sp} />
        <path d="M6 8 V16 M8 6 H14 A4 4 0 0 1 18 10" {...sp} />
      </>
    ),
    dots_h: (
      <>
        <circle cx="6" cy="12" r="1.3" fill={color} />
        <circle cx="12" cy="12" r="1.3" fill={color} />
        <circle cx="18" cy="12" r="1.3" fill={color} />
      </>
    ),
    dots_v: (
      <>
        <circle cx="12" cy="6" r="1.3" fill={color} />
        <circle cx="12" cy="12" r="1.3" fill={color} />
        <circle cx="12" cy="18" r="1.3" fill={color} />
      </>
    ),
    eye: (
      <>
        <path d="M2 12 Q7 5 12 5 Q17 5 22 12 Q17 19 12 19 Q7 19 2 12 Z" {...sp} />
        <circle cx="12" cy="12" r="3" {...sp} />
      </>
    ),
    pin: (
      <>
        <path d="M9 4 H15 L14 9 L17 12 H7 L10 9 Z" {...sp} />
        <path d="M12 12 V20" {...sp} />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="8" {...sp} />
        <path d="M12 7 V12 L15 14" {...sp} />
      </>
    ),
    check: <path d="M5 12 L10 17 L19 7" {...sp} />,
    split: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="1.5" {...sp} />
        <path d="M12 4 V20" {...sp} />
      </>
    ),
    layers: <path d="M12 3 L21 8 L12 13 L3 8 Z M3 13 L12 18 L21 13 M3 17 L12 22 L21 17" {...sp} />,
    download: <path d="M12 4 V16 M7 11 L12 16 L17 11 M4 20 H20" {...sp} />,
    upload: <path d="M12 20 V8 M7 13 L12 8 L17 13 M4 4 H20" {...sp} />,
    filter: <path d="M4 5 H20 L14 13 V19 L10 21 V13 Z" {...sp} />,
    tag: (
      <>
        <path d="M3 13 L11 21 L21 11 L13 3 H5 Q3 3 3 5 Z" {...sp} />
        <circle cx="8" cy="8" r="1.5" {...sp} />
      </>
    ),
    bug: (
      <>
        <circle cx="12" cy="13" r="5" {...sp} />
        <path d="M9 10 L7 7 M15 10 L17 7 M7 13 H3 M21 13 H17 M8 17 L5 20 M16 17 L19 20 M12 8 V18" {...sp} />
      </>
    ),
    flame: (
      <path d="M12 3 Q15 7 14 11 Q17 10 17 14 Q17 19 12 21 Q7 19 7 14 Q7 11 9 9 Q11 11 12 9 Q11 6 12 3 Z" {...sp} />
    ),
    refresh: (
      <>
        <path d="M20 8 A8 8 0 1 0 19 16" {...sp} />
        <path d="M20 4 V8 H16" {...sp} />
      </>
    ),
    undo: (
      <>
        <path d="M9 7 L4 12 L9 17" {...sp} />
        <path d="M4 12 H14 A6 6 0 0 1 14 24" {...sp} transform="translate(0,-6)" />
      </>
    ),
    paperclip: (
      <path
        d="M16 7 L9 14 A3 3 0 0 0 13 18 L20 11 A5 5 0 0 0 13 4 L5 12 A7 7 0 0 0 15 22 L18 19"
        {...sp}
      />
    ),
    // pencil / rename — a diagonal pencil with a nib line (#357).
    pencil: (
      <>
        <path d="M4 20 L8 19 L19 8 A2 2 0 0 0 16 5 L5 16 Z" {...sp} />
        <path d="M14 7 L17 10" {...sp} />
      </>
    ),
    home: (
      <>
        <path d="M3 11 L12 3 L21 11" {...sp} />
        <path d="M5 9.5 V20 H19 V9.5" {...sp} />
      </>
    ),
    // kanban — a board frame split into columns with cards (Project Management).
    kanban: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="1.5" {...sp} />
        <path d="M9 4 V20 M15 4 V20" {...sp} />
        <path d="M5 7.5 H7 M11 7.5 H13 M17 7.5 H19" {...sp} />
      </>
    ),
    quote: (
      <path
        d="M7 7 H10 V12 Q10 15 7 16 M14 7 H17 V12 Q17 15 14 16"
        {...sp}
      />
    ),
    // "open in new tab / full view" — a box with a diagonal arrow leaving it.
    external: (
      <>
        <path d="M18 13 V18 A1 1 0 0 1 17 19 H6 A1 1 0 0 1 5 18 V7 A1 1 0 0 1 6 6 H11" {...sp} />
        <path d="M14 5 H19 V10 M19 5 L11 13" {...sp} />
      </>
    ),
  };

  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      style={{ display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style }}
      aria-hidden
    >
      {/* An unregistered key (e.g. a manifest icon the set doesn't have) must not
          render a hollow <svg> — fall back to a neutral tile glyph (#456). */}
      {paths[name] ?? <rect x="4" y="4" width="16" height="16" rx="3" {...sp} />}
    </svg>
  );
}
