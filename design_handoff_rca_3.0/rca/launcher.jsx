// RCA Platform — App Launcher ( / )
// The entry screen: a gallery of app cards, each opening an app dashboard
// at /a/:slug, plus a fixed Knowledge Base link card → /kb.
//
// Reuses the RCA 3.0 design language (cream paper, hairlines, no shadows,
// Inter Tight display / Inter body / JetBrains Mono for machine-shaped text,
// one accent per context). Platform chrome stays NEUTRAL; each card expresses
// its own app color locally (derived from the single `color` hex).
//
// Uses RCA, I, RcaChip, Avatar, RCAMark from rca/system.jsx.

// ============================================================
// DATA — app manifest summaries + the fixed KB card
// `icon` demonstrates all three supported forms:
//   { form:"svg",  markup } · the app shipped its own icon.svg
//   { form:"emoji", char }  · an emoji
//   { form:"named", name }  · a key from the Icon component's set
// ============================================================
const RCA_MARK_SVG = `<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><path d="M24 78 L50 18 L76 78" fill="none" stroke="#1A1B1F" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M33 78 L50 32 L67 78" fill="none" stroke="#1A1B1F" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M42 78 L50 46 L58 78" fill="none" stroke="#1A1B1F" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M28.3 68 H71.7" fill="none" stroke="#1A1B1F" stroke-width="5" stroke-linecap="round"/><circle cx="50" cy="20" r="5.2" fill="#F0502E"/></svg>`;

const LAUNCH_APPS = [
  {
    slug: "rca",
    title: "Root Cause Analysis",
    description: "Defect root-cause analysis, paired with an AI agent.",
    icon: { form: "svg", markup: RCA_MARK_SVG },
    color: "#F0502E",
  },
  {
    slug: "yield",
    title: "Yield Monitor",
    description: "Live yield & SPC across every production line.",
    icon: { form: "named", name: "spc" },
    color: "#2D6CC9",
  },
  {
    slug: "supplier",
    title: "Supplier Quality",
    description: "Incoming inspection and supplier scorecards.",
    icon: { form: "named", name: "tag" },
    color: "#2E8B57",
  },
  {
    slug: "reliability",
    title: "Reliability Lab",
    description: "Stress, burn-in and field-return tracking.",
    icon: { form: "emoji", char: "🔥" },
    color: "#C44A3A",
  },
  {
    slug: "calibration",
    title: "Calibration",
    description: "Tool and gauge calibration schedules.",
    icon: { form: "named", name: "settings" },
    color: "#C68A2E",
  },
];

const KB_CARD = {
  slug: "kb",
  href: "/kb",
  title: "Knowledge Base",
  description: "Shared docs, SOPs and past-investigation knowledge.",
  icon: { form: "named", name: "layers" },
  color: "#5C5F66", // neutral — KB is not an app
  isKB: true,
};

// derive a pale local wash from a single hex
const softOf = (hex, pct = 8) => `color-mix(in srgb, ${hex} ${pct}%, ${RCA.white})`;

// ============================================================
// STYLES (scoped to .lnch) — hover lift + focus ring, no drop shadows
// ============================================================
if (typeof document !== "undefined" && !document.getElementById("lnch-styles")) {
  const s = document.createElement("style");
  s.id = "lnch-styles";
  s.textContent = `
    .lnch {
      --white:#FBF9F4; --paper:#F1ECE0; --paper-2:#E5E0D2; --paper-3:#D8D2C2;
      --text-paper:#1A1B1F; --text-paper-d:#5C5F66; --text-paper-d2:#8A8C90;
      --radius-card:14px;
      font-family:'Inter',system-ui,sans-serif; color:var(--text-paper);
      -webkit-font-smoothing:antialiased; letter-spacing:-0.005em;
    }
    .lnch .display { font-family:'Inter Tight','Inter',sans-serif; font-weight:700; letter-spacing:-0.025em; line-height:1.04; margin:0; }
    .lnch .mono { font-family:'JetBrains Mono',ui-monospace,monospace; letter-spacing:0; }
    .lnch .caps { font-family:'JetBrains Mono',ui-monospace,monospace; text-transform:uppercase; letter-spacing:0.14em; font-weight:500; font-size:11px; }

    .lnch-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:18px; }
    @media (max-width:1040px){ .lnch-grid{ grid-template-columns:repeat(2,1fr);} }
    @media (max-width:680px){ .lnch-grid{ grid-template-columns:1fr;} }

    .lnch-card {
      position:relative; display:flex; flex-direction:column; text-decoration:none;
      color:inherit; background:var(--white); border:1px solid var(--paper-3);
      border-radius:var(--radius-card); overflow:hidden;
      transition:transform .16s ease, border-color .16s ease, background-color .16s ease;
    }
    .lnch-card:hover { transform:translateY(-3px); border-color:var(--c); background:var(--c-soft); }
    .lnch-card:focus-visible { outline:2px solid var(--c); outline-offset:3px; }
    .lnch-card:focus { outline:none; }

    .lnch-arrow { color:var(--text-paper-d2); transition:transform .16s ease, color .16s ease; display:inline-flex; }
    .lnch-card:hover .lnch-arrow { color:var(--c); transform:translateX(4px); }
    .lnch-card:hover .lnch-slug { color:var(--text-paper-d); }

    .lnch-tile { display:flex; align-items:center; justify-content:center; flex-shrink:0; }
    .lnch-tile .lnch-svg { width:62%; height:62%; }
    .lnch-tile .lnch-svg svg { width:100%; height:100%; display:block; }

    .lnch-topbar { height:4px; width:100%; background:var(--c); }
    .lnch-card.lnch-kb { border-style:dashed; background:transparent; }
    .lnch-card.lnch-kb:hover { background:var(--c-soft); }

    .lnch-shell { min-height:100vh; display:flex; flex-direction:column; background:var(--paper); }
    .lnch-skel { pointer-events:none; }
    @keyframes lnch-shimmer { 0%{ background-position:-240px 0; } 100%{ background-position:calc(240px + 100%) 0; } }
    .lnch-bone { background:#E9E3D6 linear-gradient(90deg,#E9E3D6 0,#F4EFE4 90px,#E9E3D6 180px) repeat; background-size:240px 100%; border-radius:6px; }
    @media (prefers-reduced-motion:no-preference){ .lnch-bone { animation:lnch-shimmer 1.3s linear infinite; } }
  `;
  document.head.appendChild(s);
}

// ============================================================
// ICON TILE — renders all three forms at one optical size in one tile.
// variant: "tint" (soft wash + colored icon) | "neutral" (paper tile) | "solid" (color fill)
// ============================================================
function IconTile({ icon, color, size = 54, radius = 13, variant = "tint" }) {
  const ico = Math.round(size * 0.52);
  let bg, fg, border;
  if (variant === "tint")      { bg = softOf(color, 13); fg = color; border = `1px solid ${softOf(color, 34)}`; }
  else if (variant === "neutral") { bg = RCA.paper2;     fg = color; border = `1px solid ${RCA.paper3}`; }
  else                         { bg = color;             fg = RCA.white; border = "1px solid transparent"; }

  let inner;
  if (icon.form === "svg") {
    // The app shipped its own full-color logo. On a solid color tile we frame
    // it on a white chip so a same-colored logo can't disappear.
    if (variant === "solid") {
      inner = (
        <div style={{ width: size * 0.66, height: size * 0.66, borderRadius: radius * 0.55, background: RCA.white, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div className="lnch-svg" style={{ width: "64%", height: "64%" }} dangerouslySetInnerHTML={{ __html: icon.markup }} />
        </div>
      );
    } else {
      inner = <div className="lnch-svg" dangerouslySetInnerHTML={{ __html: icon.markup }} />;
    }
  } else if (icon.form === "emoji") {
    inner = <span style={{ fontSize: ico, lineHeight: 1, filter: variant === "solid" ? "grayscale(0)" : "none" }}>{icon.char}</span>;
  } else {
    inner = <I name={icon.name} size={ico} color={fg} strokeWidth={1.7} />;
  }

  return (
    <div className="lnch-tile" style={{ width: size, height: size, borderRadius: radius, background: bg, border }}>
      {inner}
    </div>
  );
}

// ============================================================
// PLATFORM MARK — neutral 2×2 launcher glyph (NOT painted in any app color)
// ============================================================
function PlatformMark({ size = 26, color = RCA.ink2 }) {
  const u = size * 0.4, g = size * 0.2;
  const sq = (filled) => (
    <span style={{ width: u, height: u, borderRadius: u * 0.22, background: filled ? color : "transparent", border: `1.6px solid ${color}` }} />
  );
  return (
    <span style={{ display: "inline-grid", gridTemplateColumns: `${u}px ${u}px`, gap: g, width: size, height: size }}>
      {sq(true)}{sq(false)}{sq(false)}{sq(true)}
    </span>
  );
}

function PlatformLockup({ size = 26 }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: size * 0.46 }}>
      <PlatformMark size={size} />
      <span style={{ display: "flex", flexDirection: "column", lineHeight: 1, gap: 4 }}>
        <span className="display" style={{ fontSize: size * 0.74, fontWeight: 800 }}>Workspace</span>
        <span className="caps" style={{ fontSize: size * 0.3, color: RCA.textPaperD2 }}>App Launcher</span>
      </span>
    </span>
  );
}

// ============================================================
// APP CARD — one card, three treatments matching the three directions:
//   "quiet"  → tint icon tile, color lives in the tile + hover
//   "header" → neutral tile + a top accent bar
//   "bold"   → solid color tile + a mono color chip in the footer
// ============================================================
function AppCard({ app, treatment }) {
  const href = app.href || `/a/${app.slug}`;
  const slug = app.href || `/a/${app.slug}`;
  const tileVariant = treatment === "header" ? "neutral" : treatment === "bold" ? "solid" : "tint";
  const cardStyle = { "--c": app.color, "--c-soft": softOf(app.color, app.isKB ? 6 : 7) };

  return (
    <a
      href={href}
      className={"lnch-card" + (app.isKB ? " lnch-kb" : "")}
      style={cardStyle}
      aria-label={app.isKB ? `Knowledge Base — open the knowledge base` : `Open ${app.title}`}
    >
      {treatment === "header" && !app.isKB && <span className="lnch-topbar" />}
      {treatment === "header" && app.isKB && <span className="lnch-topbar" style={{ background: RCA.paper3 }} />}

      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <IconTile icon={app.icon} color={app.color} variant={app.isKB ? "neutral" : tileVariant} />
          {app.isKB
            ? <RcaChip tone="outline" style={{ fontSize: 10 }}>Link</RcaChip>
            : null}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
          <div className="display" style={{ fontSize: 20 }}>{app.title}</div>
          <div style={{ fontSize: 13.5, color: RCA.textPaperD, lineHeight: 1.5, textWrap: "pretty" }}>{app.description}</div>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 2 }}>
          <span className="lnch-slug mono" style={{ fontSize: 11.5, color: RCA.textPaperD2 }}>{slug}</span>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {treatment === "bold" && !app.isKB && (
              <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: RCA.textPaperD2 }}>
                <span style={{ width: 9, height: 9, borderRadius: 2, background: app.color }} />
                {app.color.toUpperCase()}
              </span>
            )}
            <span className="lnch-arrow" aria-hidden="true">
              {app.isKB
                ? <ArrowUpRight size={17} />
                : <I name="arrow_r" size={17} />}
            </span>
          </span>
        </div>
      </div>
    </a>
  );
}

function ArrowUpRight({ size = 16 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} style={{ display: "block" }}>
      <path d="M7 17 L17 7 M9 7 H17 V15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// grid of app cards + the KB card (always last)
function LauncherGrid({ treatment }) {
  return (
    <div className="lnch-grid">
      {LAUNCH_APPS.map((a) => <AppCard key={a.slug} app={a} treatment={treatment} />)}
      <AppCard app={KB_CARD} treatment={treatment} />
    </div>
  );
}

// ============================================================
// DIRECTION A — "Quiet gallery": no chrome, centered, editorial.
// ============================================================
function LauncherQuiet({ width = 1080, height = 720 }) {
  return (
    <div className="lnch" style={{ width, height, background: RCA.paper, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div style={{ maxWidth: 940, width: "100%", margin: "0 auto", padding: "56px 40px 40px", display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 16, marginBottom: 40 }}>
          <PlatformMark size={30} />
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
            <div className="caps" style={{ color: RCA.textPaperD2 }}>Workspace</div>
            <h1 className="display" style={{ fontSize: 40, whiteSpace: "nowrap" }}>Choose an app</h1>
            <p style={{ fontSize: 14.5, color: RCA.textPaperD, margin: 0, maxWidth: 460, lineHeight: 1.55 }}>
              Each app is its own branded dashboard. Pick one to enter, or open the shared knowledge base.
            </p>
          </div>
        </div>
        <LauncherGrid treatment="quiet" />
      </div>
    </div>
  );
}

// ============================================================
// PLATFORM HEADER — neutral top bar (Workspace lockup · notifications · account)
// ============================================================
function PlatformHeader() {
  return (
    <div style={{ height: 60, padding: "0 28px", display: "flex", alignItems: "center", gap: 16, borderBottom: `1px solid ${RCA.paper3}`, background: RCA.paper, flexShrink: 0 }}>
      <PlatformLockup size={24} />
      <div style={{ flex: 1 }} />
      <button aria-label="Notifications" style={{ width: 36, height: 36, display: "flex", alignItems: "center", justifyContent: "center", background: "transparent", border: `1px solid ${RCA.paper3}`, borderRadius: 8, color: RCA.textPaperD, cursor: "pointer" }}>
        <I name="bell" size={16} />
      </button>
      <span style={{ display: "flex", alignItems: "center", gap: 9, padding: "4px 10px 4px 4px", border: `1px solid ${RCA.paper3}`, borderRadius: 999, cursor: "pointer" }}>
        <Avatar name="AC" size={28} />
        <span style={{ fontSize: 13, fontWeight: 500 }}>Alice Chen</span>
        <I name="chev_d" size={13} color={RCA.textPaperD} />
      </span>
    </div>
  );
}

// ============================================================
// DIRECTION B — "Platform header": full neutral top bar + top-accent-bar cards.
// ============================================================
function LauncherHeader({ width = 1280, height = 760 }) {
  return (
    <div className="lnch" style={{ width, height, background: RCA.paper, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <PlatformHeader />

      <div className="scrollable" style={{ flex: 1, overflow: "auto" }}>
        <div style={{ maxWidth: 1040, margin: "0 auto", padding: "40px 28px 48px" }}>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 28 }}>
            <div>
              <div className="caps" style={{ color: RCA.textPaperD2, marginBottom: 10 }}>Apps</div>
              <h1 className="display" style={{ fontSize: 32, whiteSpace: "nowrap" }}>Your apps</h1>
            </div>
            <span style={{ fontSize: 13, color: RCA.textPaperD }}>
              {LAUNCH_APPS.length} apps <span style={{ color: RCA.textPaperD2 }}>·</span> 1 link
            </span>
          </div>
          <LauncherGrid treatment="header" />
        </div>
      </div>
    </div>
  );
}

// ============================================================
// DIRECTION C — "Bold tiles": compact centered lockup + solid color tiles.
// ============================================================
function LauncherBold({ width = 1080, height = 720 }) {
  return (
    <div className="lnch" style={{ width, height, background: RCA.paper, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <div style={{ maxWidth: 940, width: "100%", margin: "0 auto", padding: "44px 40px 40px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 32, paddingBottom: 20, borderBottom: `1px solid ${RCA.paper3}` }}>
          <PlatformLockup size={26} />
          <span style={{ display: "flex", alignItems: "center", gap: 9, padding: "4px 10px 4px 4px", border: `1px solid ${RCA.paper3}`, borderRadius: 999, cursor: "pointer" }}>
            <Avatar name="AC" size={26} />
            <I name="chev_d" size={13} color={RCA.textPaperD} />
          </span>
        </div>
        <LauncherGrid treatment="bold" />
      </div>
    </div>
  );
}

// ============================================================
// PROTOTYPE STATES — skeleton, empty hint, responsive screen (Direction B)
// ============================================================
function SkeletonCard() {
  return (
    <div className="lnch-card lnch-skel" style={{ "--c": RCA.paper3 }} aria-hidden="true">
      <span className="lnch-topbar" style={{ background: RCA.paper3 }} />
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="lnch-bone" style={{ width: 54, height: 54, borderRadius: 13 }} />
        <div style={{ display: "flex", flexDirection: "column", gap: 9, marginTop: 2 }}>
          <div className="lnch-bone" style={{ width: "58%", height: 16 }} />
          <div className="lnch-bone" style={{ width: "92%", height: 12 }} />
          <div className="lnch-bone" style={{ width: "70%", height: 12 }} />
        </div>
        <div className="lnch-bone" style={{ width: 56, height: 12, marginTop: 4 }} />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div style={{
      border: `1px dashed ${RCA.paper3}`, borderRadius: 14, background: "transparent",
      padding: "44px 28px", display: "flex", flexDirection: "column", alignItems: "center",
      textAlign: "center", gap: 14,
    }}>
      <div style={{ width: 52, height: 52, borderRadius: 13, background: RCA.paper2, border: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <PlatformMark size={24} color={RCA.textPaperD2} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div className="display" style={{ fontSize: 19 }}>No apps yet</div>
        <p style={{ fontSize: 13.5, color: RCA.textPaperD, margin: 0, maxWidth: 380, lineHeight: 1.55 }}>
          Apps published to this workspace will appear here. In the meantime, the knowledge base is always available.
        </p>
      </div>
    </div>
  );
}

// Responsive, stateful launcher used by the clickable prototype.
// apps: array (may be empty) · loading: show skeletons
function LauncherScreenB({ apps = LAUNCH_APPS, loading = false }) {
  const count = apps.length;
  const isEmpty = !loading && count === 0;
  return (
    <div className="lnch lnch-shell">
      <PlatformHeader />
      <div className="scrollable" style={{ flex: 1, overflow: "auto" }}>
        <div style={{ maxWidth: 1100, margin: "0 auto", padding: "40px 28px 56px" }}>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 28, gap: 16 }}>
            <div>
              <div className="caps" style={{ color: RCA.textPaperD2, marginBottom: 10 }}>Apps</div>
              <h1 className="display" style={{ fontSize: 32, whiteSpace: "nowrap" }}>Your apps</h1>
            </div>
            <span style={{ fontSize: 13, color: RCA.textPaperD, whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 8 }}>
              {loading
                ? <><span style={{ width: 7, height: 7, borderRadius: "50%", background: RCA.accent, animation: "lnch-shimmer 1s linear infinite" }} /> Loading apps…</>
                : <>{count} {count === 1 ? "app" : "apps"} <span style={{ color: RCA.textPaperD2 }}>·</span> 1 link</>}
            </span>
          </div>

          {loading ? (
            <div className="lnch-grid">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : isEmpty ? (
            <div className="lnch-grid">
              <div style={{ gridColumn: "1 / -1" }}><EmptyState /></div>
              <AppCard app={KB_CARD} treatment="header" />
            </div>
          ) : (
            <div className="lnch-grid">
              {apps.map((a) => <AppCard key={a.slug} app={a} treatment="header" />)}
              <AppCard app={KB_CARD} treatment="header" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, {
  LAUNCH_APPS, KB_CARD,
  IconTile, PlatformMark, PlatformLockup, PlatformHeader, AppCard, LauncherGrid,
  LauncherQuiet, LauncherHeader, LauncherBold,
  SkeletonCard, EmptyState, LauncherScreenB,
});
