// RCA 3.0 — brand system primitives
// Colors, type, and reusable hi-fi components.
// Load via <script type="text/babel" src="rca/system.jsx"></script>

// ============================================================
// TOKENS
// ============================================================
const RCA = {
  // surfaces
  ink:      "#16181D",   // theme dark
  ink2:     "#1A1B1F",   // stroke on light
  ink3:     "#23262E",   // elevated dark surfaces
  ink4:     "#2E323B",   // hover/border dark
  paper:    "#F1ECE0",   // primary cream
  paper2:   "#E5E0D2",   // alt cream / chip bg
  paper3:   "#D8D2C2",   // border on paper
  white:    "#FBF9F4",   // pure paper-ish

  // accent
  accent:   "#F0502E",   // RCA orange dot
  accentH:  "#D8431F",   // hover
  accentSoft:"#FCE4DC",  // very pale orange wash

  // text
  textPaper:  "#1A1B1F",     // text on paper
  textPaperD: "#5C5F66",     // dim text on paper
  textPaperD2:"#8A8C90",     // dimmer
  textDark:   "#F1ECE0",     // text on dark
  textDarkD:  "#9CA0AB",     // dim text on dark
  textDarkD2: "#6A6E78",

  // semantic
  ok:    "#3A8A4A",
  warn:  "#C68A2E",
  err:   "#C44A3A",
  info:  "#2D6CC9",

  // type scale
  fSans:  "'Inter Tight', 'Inter', system-ui, -apple-system, sans-serif",
  fBody:  "'Inter', system-ui, -apple-system, sans-serif",
  fMono:  "'JetBrains Mono', ui-monospace, Menlo, monospace",
};

// inject global stylesheet once
if (typeof document !== "undefined" && !document.getElementById("rca-styles")) {
  const s = document.createElement("style");
  s.id = "rca-styles";
  s.textContent = `
    .rca, .rca *, .rca *::before, .rca *::after { box-sizing: border-box; }
    .rca {
      font-family: ${RCA.fBody};
      color: ${RCA.textPaper};
      -webkit-font-smoothing: antialiased;
      letter-spacing: -0.005em;
    }
    .rca .display, .rca h1, .rca h2, .rca h3 {
      font-family: ${RCA.fSans};
      font-weight: 700;
      letter-spacing: -0.025em;
      line-height: 1.05;
      margin: 0;
    }
    .rca .mono { font-family: ${RCA.fMono}; letter-spacing: 0; }
    .rca .caps { text-transform: uppercase; letter-spacing: 0.12em; font-family: ${RCA.fMono}; font-weight: 500; font-size: 11px; }
    .rca .scrollable { overflow: auto; scrollbar-width: thin; scrollbar-color: ${RCA.paper3} transparent; }
    .rca .scrollable::-webkit-scrollbar { width: 8px; height: 8px; }
    .rca .scrollable::-webkit-scrollbar-thumb { background: ${RCA.paper3}; border-radius: 4px; }
    .rca .scrollable::-webkit-scrollbar-track { background: transparent; }
    .rca-dark { background: ${RCA.ink}; color: ${RCA.textDark}; }
    .rca-dark .scrollable { scrollbar-color: ${RCA.ink4} transparent; }
    .rca-dark .scrollable::-webkit-scrollbar-thumb { background: ${RCA.ink4}; }
  `;
  document.head.appendChild(s);
}

// ============================================================
// LOGO
// ============================================================
function RCAMark({ size = 24, color = RCA.ink2, dot = RCA.accent }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" style={{ display: "block" }}>
      <path d="M24 78 L50 18 L76 78" fill="none" stroke={color} strokeWidth="6.4" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M33 78 L50 32 L67 78" fill="none" stroke={color} strokeWidth="6.4" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M42 78 L50 46 L58 78" fill="none" stroke={color} strokeWidth="6.4" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M28.3 68 H71.7" fill="none" stroke={color} strokeWidth="6.4" strokeLinecap="round"/>
      <circle cx="50" cy="20" r="6.4" fill={dot}/>
    </svg>
  );
}

function RCALockup({ size = 28, onDark, compact, version = "3.0" }) {
  const ink = onDark ? RCA.textDark : RCA.ink2;
  const dim = onDark ? RCA.textDarkD : RCA.textPaperD;
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: size * 0.5 }}>
      <RCAMark size={size} color={ink}/>
      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 4, fontFamily: RCA.fSans, fontWeight: 800, fontSize: size * 0.85, color: ink, letterSpacing: "-0.03em" }}>
          <span>RCA</span>
          <span style={{ width: size * 0.16, height: size * 0.16, background: RCA.accent, marginLeft: size * 0.1, marginRight: size * 0.05, display: "inline-block", transform: "translateY(-2px)" }}/>
          <span>{version}</span>
        </div>
        {!compact && (
          <div style={{ fontFamily: RCA.fMono, fontSize: size * 0.34, color: dim, letterSpacing: "0.32em", marginTop: 4, textTransform: "uppercase" }}>
            Analysis · AI · Agent
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// COMPONENTS
// ============================================================

// Button — variants: primary | secondary | ghost | dark
function Btn({ children, variant = "secondary", size = "md", onDark, icon, iconRight, style, fullWidth, disabled, onClick, title }) {
  const sizes = {
    sm: { h: 28, px: 10, fs: 12, gap: 6, ic: 13 },
    md: { h: 36, px: 14, fs: 13, gap: 8, ic: 15 },
    lg: { h: 44, px: 18, fs: 14, gap: 10, ic: 17 },
  }[size];
  const variants = {
    primary: { bg: RCA.accent, fg: RCA.white, bd: "transparent" },
    secondary: onDark
      ? { bg: "transparent", fg: RCA.textDark, bd: RCA.ink4 }
      : { bg: "transparent", fg: RCA.ink2, bd: RCA.paper3 },
    ghost: onDark
      ? { bg: "transparent", fg: RCA.textDarkD, bd: "transparent" }
      : { bg: "transparent", fg: RCA.textPaperD, bd: "transparent" },
    dark: { bg: RCA.ink, fg: RCA.textDark, bd: "transparent" },
    solid: onDark
      ? { bg: RCA.white, fg: RCA.ink, bd: "transparent" }
      : { bg: RCA.ink, fg: RCA.textDark, bd: "transparent" },
  }[variant];
  return (
    <button disabled={disabled} onClick={onClick} title={title} style={{
      display: "inline-flex", alignItems: "center", gap: sizes.gap, justifyContent: "center",
      height: sizes.h, padding: `0 ${sizes.px}px`,
      background: variants.bg, color: variants.fg,
      border: `1px solid ${variants.bd}`, borderRadius: 6,
      fontFamily: RCA.fBody, fontSize: sizes.fs, fontWeight: 500,
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.45 : 1,
      width: fullWidth ? "100%" : "auto",
      whiteSpace: "nowrap",
      transition: "background .15s, color .15s",
      ...style,
    }}>
      {icon && <span style={{ display: "inline-flex" }}>{icon}</span>}
      {children}
      {iconRight && <span style={{ display: "inline-flex", opacity: 0.6 }}>{iconRight}</span>}
    </button>
  );
}

// Chip / tag
function RcaChip({ children, tone = "default", onDark, style, dot, icon }) {
  const palette = {
    default: onDark ? { bg: RCA.ink3, fg: RCA.textDarkD, bd: RCA.ink4 } : { bg: RCA.paper2, fg: RCA.textPaperD, bd: "transparent" },
    accent:  { bg: RCA.accentSoft, fg: RCA.accentH, bd: "transparent" },
    accentSolid: { bg: RCA.accent, fg: RCA.white, bd: "transparent" },
    ok:      { bg: "rgba(58,138,74,.12)", fg: RCA.ok, bd: "transparent" },
    warn:    { bg: "rgba(198,138,46,.14)", fg: RCA.warn, bd: "transparent" },
    err:     { bg: "rgba(196,74,58,.12)", fg: RCA.err, bd: "transparent" },
    outline: onDark ? { bg: "transparent", fg: RCA.textDarkD, bd: RCA.ink4 } : { bg: "transparent", fg: RCA.textPaperD, bd: RCA.paper3 },
  }[tone];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "2px 8px", borderRadius: 4,
      background: palette.bg, color: palette.fg, border: `1px solid ${palette.bd}`,
      fontFamily: RCA.fMono, fontSize: 11, fontWeight: 500,
      letterSpacing: "0.02em",
      whiteSpace: "nowrap",
      ...style,
    }}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: palette.fg, opacity: 0.85 }}/>}
      {icon}
      {children}
    </span>
  );
}

// Card
function Card({ children, style, onDark, padded, hoverable }) {
  return (
    <div style={{
      background: onDark ? RCA.ink3 : RCA.white,
      border: `1px solid ${onDark ? RCA.ink4 : RCA.paper3}`,
      borderRadius: 8,
      padding: padded ?? 16,
      ...style,
    }}>{children}</div>
  );
}

// Status dot
function StatDot({ tone = "ok", size = 8 }) {
  const c = { ok: RCA.ok, warn: RCA.warn, err: RCA.err, info: RCA.info, accent: RCA.accent, mute: RCA.textPaperD2 }[tone];
  return <span style={{ display: "inline-block", width: size, height: size, borderRadius: "50%", background: c }}/>;
}

// Section header (caps label + optional action)
function CapsLabel({ children, style, onDark }) {
  return (
    <div className="caps" style={{ color: onDark ? RCA.textDarkD : RCA.textPaperD, fontSize: 11, ...style }}>
      {children}
    </div>
  );
}

// Avatar
function Avatar({ name = "Aa", size = 24, onDark }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: "50%",
      background: onDark ? RCA.ink4 : RCA.paper2,
      color: onDark ? RCA.textDark : RCA.textPaper,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      fontFamily: RCA.fBody, fontWeight: 600, fontSize: size * 0.4,
      border: `1px solid ${onDark ? RCA.ink3 : RCA.paper3}`,
    }}>{name.slice(0, 2)}</div>
  );
}

// Icons — clean line set
function I({ name, size = 16, color = "currentColor", strokeWidth = 1.6, style }) {
  const s = { width: size, height: size, display: "inline-block", verticalAlign: "middle", flexShrink: 0, ...style };
  const sp = { fill: "none", stroke: color, strokeWidth, strokeLinecap: "round", strokeLinejoin: "round" };
  const paths = {
    search: <><circle cx="10" cy="10" r="6" {...sp}/><path d="M14.5 14.5 L20 20" {...sp}/></>,
    plus: <path d="M12 4 V20 M4 12 H20" {...sp}/>,
    minus: <path d="M4 12 H20" {...sp}/>,
    x: <path d="M5 5 L19 19 M5 19 L19 5" {...sp}/>,
    chev_d: <path d="M6 9 L12 15 L18 9" {...sp}/>,
    chev_r: <path d="M9 6 L15 12 L9 18" {...sp}/>,
    chev_l: <path d="M15 6 L9 12 L15 18" {...sp}/>,
    folder: <path d="M3 7 V18 A1 1 0 0 0 4 19 H20 A1 1 0 0 0 21 18 V9 A1 1 0 0 0 20 8 H11 L9 5 H4 A1 1 0 0 0 3 6 Z" {...sp}/>,
    file: <><path d="M6 3 H14 L18 7 V20 A1 1 0 0 1 17 21 H7 A1 1 0 0 1 6 20 Z" {...sp}/><path d="M14 3 V7 H18" {...sp}/></>,
    chat: <path d="M4 5 H20 V15 H10 L5 19 V15 H4 Z" {...sp}/>,
    play: <path d="M7 5 L19 12 L7 19 Z" {...sp}/>,
    term: <><rect x="3" y="4" width="18" height="16" rx="1.5" {...sp}/><path d="M6 9 L9 12 L6 15 M11 15 H17" {...sp}/></>,
    user: <><circle cx="12" cy="8" r="3.5" {...sp}/><path d="M5 20 Q5 14 12 14 Q19 14 19 20" {...sp}/></>,
    users: <><circle cx="9" cy="9" r="3" {...sp}/><circle cx="17" cy="10" r="2.5" {...sp}/><path d="M3 19 Q3 14 9 14 Q15 14 15 19 M15 19 Q21 19 21 15 Q21 12 17 12" {...sp}/></>,
    settings: <><circle cx="12" cy="12" r="2.5" {...sp}/><path d="M12 3 L13 5 L15 4 L15 6 L17 6 L16 8 L18 9 L17 11 L19 12 L17 13 L18 15 L16 16 L17 18 L15 18 L15 20 L13 19 L12 21 L11 19 L9 20 L9 18 L7 18 L8 16 L6 15 L7 13 L5 12 L7 11 L6 9 L8 8 L7 6 L9 6 L9 4 L11 5 Z" {...sp}/></>,
    bell: <><path d="M6 16 V11 A6 6 0 0 1 18 11 V16 L20 18 H4 Z" {...sp}/><path d="M10 21 H14" {...sp}/></>,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1" {...sp}/><rect x="14" y="4" width="6" height="6" rx="1" {...sp}/><rect x="4" y="14" width="6" height="6" rx="1" {...sp}/><rect x="14" y="14" width="6" height="6" rx="1" {...sp}/></>,
    table: <><rect x="3" y="5" width="18" height="14" rx="1" {...sp}/><path d="M3 10 H21 M9 5 V19 M15 5 V19" {...sp}/></>,
    chart: <path d="M4 20 H20 M7 20 V13 M11 20 V8 M15 20 V11 M19 20 V5" {...sp}/>,
    pareto: <><path d="M4 20 H20" {...sp}/><rect x="5" y="8" width="2.5" height="12" {...sp}/><rect x="9" y="11" width="2.5" height="9" {...sp}/><rect x="13" y="14" width="2.5" height="6" {...sp}/><rect x="17" y="17" width="2.5" height="3" {...sp}/><path d="M5 8 L9 11 L13 14 L17 17 L19 19" stroke={RCA.accent} {...sp} fill="none"/></>,
    fishbone: <><path d="M3 12 H21" {...sp}/><path d="M7 12 L9 6 M11 12 L13 6 M15 12 L17 6 M7 12 L9 18 M11 12 L13 18 M15 12 L17 18" {...sp}/><circle cx="21" cy="12" r="1.5" {...sp}/></>,
    spc: <path d="M4 12 Q6 8 8 12 T12 12 T16 7 T20 12 M3 18 H21 M3 6 H21" {...sp} fill="none"/>,
    photo: <><rect x="3" y="5" width="18" height="14" rx="1" {...sp}/><circle cx="9" cy="11" r="2" {...sp}/><path d="M3 17 L9 12 L13 15 L17 11 L21 14" {...sp}/></>,
    bug: <><circle cx="12" cy="13" r="5" {...sp}/><path d="M9 10 L7 7 M15 10 L17 7 M7 13 H3 M21 13 H17 M8 17 L5 20 M16 17 L19 20 M12 8 V18" {...sp}/></>,
    branch: <><circle cx="6" cy="6" r="2" {...sp}/><circle cx="6" cy="18" r="2" {...sp}/><circle cx="18" cy="9" r="2" {...sp}/><path d="M6 8 V16 M6 9 Q6 12 12 12 Q18 12 18 11" {...sp}/></>,
    lock: <><rect x="5" y="11" width="14" height="10" rx="1" {...sp}/><path d="M8 11 V7 A4 4 0 0 1 16 7 V11" {...sp}/></>,
    globe: <><circle cx="12" cy="12" r="9" {...sp}/><ellipse cx="12" cy="12" rx="4" ry="9" {...sp}/><path d="M3 12 H21" {...sp}/></>,
    sparkle: <><path d="M12 3 L13 9 L19 10 L13 11 L12 17 L11 11 L5 10 L11 9 Z" {...sp} fill={color} fillOpacity="0.0"/><path d="M19 4 L20 6 L22 7 L20 8 L19 10 L18 8 L16 7 L18 6 Z" {...sp}/></>,
    arrow_r: <path d="M5 12 H19 M14 7 L19 12 L14 17" {...sp}/>,
    arrow_u: <path d="M12 19 V5 M7 10 L12 5 L17 10" {...sp}/>,
    arrow_d: <path d="M12 5 V19 M7 14 L12 19 L17 14" {...sp}/>,
    git: <><circle cx="6" cy="6" r="2" {...sp}/><circle cx="6" cy="18" r="2" {...sp}/><circle cx="18" cy="12" r="2" {...sp}/><path d="M6 8 V16 M8 6 H14 A4 4 0 0 1 18 10" {...sp}/></>,
    star: <path d="M12 4 L14.5 9 L20 10 L16 14 L17 19.5 L12 17 L7 19.5 L8 14 L4 10 L9.5 9 Z" {...sp}/>,
    dots_h: <><circle cx="6" cy="12" r="1.3" fill={color}/><circle cx="12" cy="12" r="1.3" fill={color}/><circle cx="18" cy="12" r="1.3" fill={color}/></>,
    dots_v: <><circle cx="12" cy="6" r="1.3" fill={color}/><circle cx="12" cy="12" r="1.3" fill={color}/><circle cx="12" cy="18" r="1.3" fill={color}/></>,
    eye: <><path d="M2 12 Q7 5 12 5 Q17 5 22 12 Q17 19 12 19 Q7 19 2 12 Z" {...sp}/><circle cx="12" cy="12" r="3" {...sp}/></>,
    pin: <><path d="M9 4 H15 L14 9 L17 12 H7 L10 9 Z" {...sp}/><path d="M12 12 V20" {...sp}/></>,
    clock: <><circle cx="12" cy="12" r="8" {...sp}/><path d="M12 7 V12 L15 14" {...sp}/></>,
    check: <path d="M5 12 L10 17 L19 7" {...sp}/>,
    split: <><rect x="3" y="4" width="18" height="16" rx="1.5" {...sp}/><path d="M12 4 V20" {...sp}/></>,
    layers: <path d="M12 3 L21 8 L12 13 L3 8 Z M3 13 L12 18 L21 13 M3 17 L12 22 L21 17" {...sp}/>,
    download: <path d="M12 4 V16 M7 11 L12 16 L17 11 M4 20 H20" {...sp}/>,
    upload: <path d="M12 20 V8 M7 13 L12 8 L17 13 M4 4 H20" {...sp}/>,
    filter: <path d="M4 5 H20 L14 13 V19 L10 21 V13 Z" {...sp}/>,
    tag: <><path d="M3 13 L11 21 L21 11 L13 3 H5 Q3 3 3 5 Z" {...sp}/><circle cx="8" cy="8" r="1.5" {...sp}/></>,
    flame: <path d="M12 3 Q15 7 14 11 Q17 10 17 14 Q17 19 12 21 Q7 19 7 14 Q7 11 9 9 Q11 11 12 9 Q11 6 12 3 Z" {...sp}/>,
    book: <><path d="M4 5 Q4 4 5 4 H11 V20 H5 Q4 20 4 19 Z" {...sp}/><path d="M20 5 Q20 4 19 4 H13 V20 H19 Q20 20 20 19 Z" {...sp}/></>,
    link: <><path d="M10 14 A4 4 0 0 0 14 14 L17 11 A4 4 0 0 0 11 5 L9.5 6.5" {...sp}/><path d="M14 10 A4 4 0 0 0 10 10 L7 13 A4 4 0 0 0 13 19 L14.5 17.5" {...sp}/></>,
    refresh: <><path d="M20 11 A8 8 0 0 0 6 6 L4 8 M4 4 V8 H8" {...sp}/><path d="M4 13 A8 8 0 0 0 18 18 L20 16 M20 20 V16 H16" {...sp}/></>,
    node: <><circle cx="6" cy="6" r="2.5" {...sp}/><circle cx="18" cy="6" r="2.5" {...sp}/><circle cx="12" cy="18" r="2.5" {...sp}/><path d="M7.5 7.5 L11 15.5 M16.5 7.5 L13 15.5 M8 6 H16" {...sp}/></>,
    collapse: <><path d="M8 6 L12 10 L16 6" {...sp}/><path d="M8 18 L12 14 L16 18" {...sp}/></>,
    pencil: <><path d="M4 20 L4 16 L15 5 L19 9 L8 20 Z" {...sp}/><path d="M13 7 L17 11" {...sp}/></>,
    trash: <><path d="M5 7 H19 M10 7 V5 A1 1 0 0 1 11 4 H13 A1 1 0 0 1 14 5 V7 M6 7 L7 20 A1 1 0 0 0 8 21 H16 A1 1 0 0 0 17 20 L18 7" {...sp}/><path d="M10 11 V17 M14 11 V17" {...sp}/></>,
    save: <><path d="M5 4 H16 L20 8 V19 A1 1 0 0 1 19 20 H5 A1 1 0 0 1 4 19 V5 A1 1 0 0 1 5 4 Z" {...sp}/><path d="M8 4 V9 H15 V4 M8 20 V14 H16 V20" {...sp}/></>,
    file_plus: <><path d="M6 3 H14 L18 7 V20 A1 1 0 0 1 17 21 H7 A1 1 0 0 1 6 20 Z" {...sp}/><path d="M14 3 V7 H18 M12 11 V17 M9 14 H15" {...sp}/></>,
    folder_plus: <><path d="M3 7 V18 A1 1 0 0 0 4 19 H20 A1 1 0 0 0 21 18 V9 A1 1 0 0 0 20 8 H11 L9 5 H4 A1 1 0 0 0 3 6 Z" {...sp}/><path d="M12 11 V16 M9.5 13.5 H14.5" {...sp}/></>,
    rename: <><rect x="3" y="6" width="18" height="12" rx="1.5" {...sp}/><path d="M7 9 V15 M5.5 9 H8.5 M5.5 15 H8.5 M12 8 V16" {...sp}/></>,
    dots_v: <><circle cx="12" cy="5" r="1.4" fill={color} stroke="none"/><circle cx="12" cy="12" r="1.4" fill={color} stroke="none"/><circle cx="12" cy="19" r="1.4" fill={color} stroke="none"/></>,
  };
  return <svg viewBox="0 0 24 24" style={s}>{paths[name] || null}</svg>;
}

// Striped image placeholder
function Hatch2({ w = "100%", h = 100, label, onDark, style }) {
  const bg = onDark ? RCA.ink3 : RCA.paper2;
  const line = onDark ? "rgba(255,255,255,.04)" : "rgba(0,0,0,.06)";
  return (
    <div style={{
      width: w, height: h, position: "relative",
      background: `${bg} repeating-linear-gradient(45deg, transparent 0 8px, ${line} 8px 9px)`,
      border: `1px dashed ${onDark ? RCA.ink4 : RCA.paper3}`,
      borderRadius: 6,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: RCA.fMono, fontSize: 11, color: onDark ? RCA.textDarkD2 : RCA.textPaperD2,
      letterSpacing: "0.08em",
      ...style,
    }}>{label}</div>
  );
}

// Mini sparkline (random-ish)
function Sparkline({ w = 100, h = 24, color = RCA.accent, points }) {
  const pts = points || [4, 6, 5, 8, 7, 10, 8, 12, 9, 13, 11, 14];
  const max = Math.max(...pts);
  const min = Math.min(...pts);
  const step = w / (pts.length - 1);
  const d = pts.map((v, i) => `${i === 0 ? "M" : "L"} ${i * step} ${h - ((v - min) / (max - min || 1)) * (h - 4) - 2}`).join(" ");
  return <svg width={w} height={h}><path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}

Object.assign(window, {
  RCA, RCAMark, RCALockup,
  Btn, RcaChip, Card, StatDot, CapsLabel, Avatar, I, Hatch2, Sparkline,
});
