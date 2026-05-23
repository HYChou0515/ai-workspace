/**
 * Inline SVG mark — keeps the orange dot at the apex and matches
 * design_handoff_rca_3.0/rca/system.jsx's RCAMark. Use as a React
 * component when stroke/dot need to follow `color` (e.g. dark backgrounds).
 * For static use, /rca-mark.svg in <img> is equally fine.
 */

export function RcaMark({
  size = 24,
  color = "var(--ink-2)",
  dot = "var(--accent)",
}: {
  size?: number;
  color?: string;
  dot?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ display: "block", flexShrink: 0 }}
      aria-hidden
    >
      <path
        d="M24 78 L50 18 L76 78"
        fill="none"
        stroke={color}
        strokeWidth="6.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M33 78 L50 32 L67 78"
        fill="none"
        stroke={color}
        strokeWidth="6.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M42 78 L50 46 L58 78"
        fill="none"
        stroke={color}
        strokeWidth="6.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M28.3 68 H71.7"
        fill="none"
        stroke={color}
        strokeWidth="6.4"
        strokeLinecap="round"
      />
      <circle cx="50" cy="20" r="6.4" fill={dot} />
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
  const fg = onDark ? "var(--text-dark)" : "var(--ink-2)";
  const dim = onDark ? "var(--text-dark-d)" : "var(--text-paper-d)";
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: size * 0.5 }}>
      <RcaMark size={size} color={fg} />
      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 4,
            fontFamily: "var(--font-display)",
            fontWeight: 800,
            fontSize: size * 0.85,
            color: fg,
            letterSpacing: "-0.03em",
          }}
        >
          <span>RCA</span>
          <span
            style={{
              width: size * 0.16,
              height: size * 0.16,
              background: "var(--accent)",
              marginLeft: size * 0.1,
              marginRight: size * 0.05,
              display: "inline-block",
              transform: "translateY(-2px)",
            }}
          />
          <span>3.0</span>
        </div>
        {!compact && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: size * 0.34,
              color: dim,
              letterSpacing: "0.32em",
              marginTop: 4,
              textTransform: "uppercase",
            }}
          >
            Analysis · AI · Agent
          </div>
        )}
      </div>
    </div>
  );
}
