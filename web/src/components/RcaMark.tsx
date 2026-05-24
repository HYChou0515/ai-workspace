/**
 * Inline SVG mark — keeps the orange dot at the apex and matches
 * design_handoff_rca_3.0/rca/system.jsx's RCAMark. Use as a React
 * component when stroke/dot need to follow `color` (e.g. dark backgrounds).
 * For static use, /rca-mark.svg in <img> is equally fine.
 */

export function RcaMark({
  size = 24,
  color = "var(--brand-mark)",
  dot = "var(--accent)",
  animate = false,
}: {
  size?: number;
  color?: string;
  dot?: string;
  /** Draw the chevrons in + pop the apex dot (the brand entry animation). */
  animate?: boolean;
}) {
  // pathLength=1 normalizes the dash math across the differently-long strokes
  // so brand.css can stagger them with one keyframe (see .rca-mark-draw).
  const stroke = animate ? { pathLength: 1, className: "rca-stroke" } : {};
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className={animate ? "rca-mark-draw" : undefined}
      style={{ display: "block", flexShrink: 0 }}
      aria-hidden
    >
      <path
        d="M24 78 L50 18 L76 78"
        fill="none"
        stroke={color}
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        {...stroke}
      />
      <path
        d="M33 78 L50 32 L67 78"
        fill="none"
        stroke={color}
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        {...stroke}
      />
      <path
        d="M42 78 L50 46 L58 78"
        fill="none"
        stroke={color}
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        {...stroke}
      />
      <path
        d="M28.3 68 H71.7"
        fill="none"
        stroke={color}
        strokeWidth="3.4"
        strokeLinecap="round"
        {...stroke}
      />
      <circle cx="50" cy="20" r="4.1" fill={dot} className={animate ? "rca-dot" : undefined} />
    </svg>
  );
}

/**
 * Brand lockup — mark + "RCA · 3.0" text + caps subtitle.
 * Matches the design's RCALockup in system.jsx.
 */
export function RcaLockup({
  size = 28,
  onDark = false,
  compact = false,
}: {
  size?: number;
  onDark?: boolean;
  compact?: boolean;
}) {
  // onDark forces the on-ink palette (e.g. a dark ribbon regardless of theme);
  // otherwise follow the theme via the flipping tokens.
  const fg = onDark ? "var(--text-dark)" : "var(--brand-mark)";
  const dim = onDark ? "var(--text-dark-d)" : "var(--text-paper-d)";
  // `size` is the MARK size. The chevron fills ~60% of its box, so the design
  // pairs a big mark with smaller text (mark 40 / wordmark 24 / subtitle 8.5).
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: size * 0.28 }}>
      <RcaMark size={size} color={fg} />
      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 2,
            fontFamily: "var(--font-display)",
            fontWeight: 800,
            fontSize: size * 0.6,
            color: fg,
            letterSpacing: "-0.03em",
          }}
        >
          <span>RCA</span>
          <span
            style={{
              width: size * 0.125,
              height: size * 0.125,
              background: "var(--accent)",
              marginLeft: size * 0.1,
              marginRight: size * 0.05,
              display: "inline-block",
              transform: `translateY(-${size * 0.06}px)`,
            }}
          />
          <span>3.0</span>
        </div>
        {!compact && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: size * 0.21,
              color: dim,
              letterSpacing: "0.3em",
              marginTop: size * 0.12,
              textTransform: "uppercase",
              whiteSpace: "nowrap",
            }}
          >
            Analysis · AI · Agent
          </div>
        )}
      </div>
    </div>
  );
}
