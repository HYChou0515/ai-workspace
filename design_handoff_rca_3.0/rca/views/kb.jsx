// Knowledge Base — collections + drawer + management page + chat history

// ============================================================
// Sample data — collections are the unit of KB search
// ============================================================
const KB_COLLECTIONS = [
  { id: "col-1", title: "Past investigations",      icon: "sparkle", docs: 142, size: "38 MB",  updated: "12 min ago", owner: { name: "auto",       initials: "—"  }, shared: "org",     desc: "All resolved & abandoned RCAs. Auto-added when a case closes.", pinned: true, auto: true, cited: 92 },
  { id: "col-2", title: "Reflow process notes",     icon: "flame",   docs: 24,  size: "6.1 MB", updated: "yesterday",  owner: { name: "Alice Chen", initials: "AC" }, shared: "org",     desc: "PID tuning runbooks, zone profiles, change-log.", pinned: true, cited: 47 },
  { id: "col-3", title: "SOPs · SMT line",          icon: "file",    docs: 38,  size: "12 MB",  updated: "3 days ago", owner: { name: "Carol Kao",  initials: "CK" }, shared: "org",     desc: "Standard operating procedures uploaded by QA team.", cited: 28 },
  { id: "col-4", title: "Equipment manuals",        icon: "settings",docs: 64,  size: "310 MB", updated: "1 wk ago",   owner: { name: "Bob Liu",    initials: "BL" }, shared: "org",     desc: "Vendor PDFs for reflow ovens, AOI, SMT placers.", cited: 19 },
  { id: "col-5", title: "IPC standards",            icon: "check",   docs: 27,  size: "180 MB", updated: "1 mo ago",   owner: { name: "Carol Kao",  initials: "CK" }, shared: "org",     desc: "IPC-A-610, J-STD-001, etc. Manually uploaded by quality.", cited: 34 },
  { id: "col-6", title: "My drafts",                icon: "star",    docs: 7,   size: "2.3 MB", updated: "today",      owner: { name: "Alice Chen", initials: "AC" }, shared: "private", desc: "Personal notes I'm trying out.", cited: 2 },
  { id: "col-7", title: "Battery investigations",   icon: "bug",     docs: 18,  size: "4.8 MB", updated: "yesterday",  owner: { name: "Dan J.",     initials: "DJ" }, shared: "team",    desc: "18650 cell tests + fixture maintenance logs.", cited: 11 },
  { id: "col-8", title: "Supplier reports — Q1",    icon: "table",   docs: 14,  size: "42 MB",  updated: "2 wk ago",   owner: { name: "Bob Liu",    initials: "BL" }, shared: "team",    desc: "Quarterly supplier QA reports.", cited: 6 },
];

const KB_SUGGESTIONS = [
  "What does IPC-A-610 say about void rate acceptance?",
  "Has reflow zone-3 drift been seen before?",
  "Show me 5-Why chains where the root was in change-control",
  "Summarize the last 3 wirebond investigations",
];

const CHAT_HISTORY = [
  { id: "c-12", title: "Void acceptance thresholds for BGA",     msgs: 14, updated: "12 min ago", pinned: true, snippet: "What does IPC-A-610 say about void rate…" },
  { id: "c-11", title: "Past zone-3 drift incidents",            msgs: 8,  updated: "2 h ago",    pinned: true, snippet: "Has reflow zone-3 drift been seen before?" },
  { id: "c-10", title: "Wirebond pull-strength patterns",        msgs: 22, updated: "yesterday",                snippet: "Trends across last 6 months on Sensor V2…" },
  { id: "c-9",  title: "Change-control coverage gaps",           msgs: 6,  updated: "yesterday",                snippet: "Find investigations where root was matrix gap" },
  { id: "c-8",  title: "Reflow PID gains — best practices",      msgs: 18, updated: "2 days ago" },
  { id: "c-7",  title: "Defect taxonomy proposal",               msgs: 31, updated: "3 days ago",               snippet: "Should we split 'void' into BGA-pad vs QFN-center?" },
  { id: "c-5",  title: "Top yield drops Q3",                     msgs: 9,  updated: "1 wk ago" },
  { id: "c-4",  title: "Onboarding: how to write an RCA",        msgs: 14, updated: "2 wk ago" },
];

// ============================================================
// 1) KB CHAT DRAWER — collection picker + chat
// ============================================================
function KBDrawer({ open, onClose, onOpenChats, onOpenKB }) {
  const [activeCols, setActiveCols] = React.useState(["col-1", "col-2", "col-5"]);
  const [messages, setMessages] = React.useState([
    { role: "agent", text: "Hi — ask me anything. I'll search the collections you've selected and cite what I find." },
  ]);
  const [pending, setPending] = React.useState(false);

  const toggleCol = (id) => setActiveCols((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id]);
  const totalDocs = KB_COLLECTIONS.filter((c) => activeCols.includes(c.id)).reduce((s, c) => s + c.docs, 0);

  const send = (text) => {
    setMessages((m) => [...m, { role: "user", who: "AC", time: "now", text }]);
    setPending(true);
    setTimeout(() => {
      setMessages((m) => [...m, {
        role: "agent",
        text: <>
          <p style={{ margin: 0, marginBottom: 8 }}>
            IPC-A-610 sets <strong>Class 2 acceptance at &lt; 25% voiding</strong> for BGA solder joints; Class 3 (high-rel) at <strong>&lt; 9%</strong>. Your MX-7 void rate of 3.2% is well within either spec, but the 2.3× <em>jump</em> from 1.4% baseline is the alarm — drift, not absolute threshold.
          </p>
        </>,
        citations: [
          { col: "IPC standards",      n: 12, snippet: "Class 2 BGA voiding ≤ 25% by area" },
          { col: "Past investigations", n: 3, snippet: "INC-0119, INC-0098, INC-0072 — drift-driven, baseline = 1.4±0.3%" },
          { col: "Reflow process notes", n: 1, snippet: "PID tuning runbook · zone-3 ± 1°C envelope" },
        ],
      }]);
      setPending(false);
    }, 900);
  };

  if (!open) return null;
  return (
    <>
      <div onClick={onClose} style={{
        position: "fixed", inset: 0,
        background: "rgba(20,22,28,0.35)",
        backdropFilter: "blur(2px)",
        zIndex: 60,
        animation: "kbFade 200ms ease",
      }}/>
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: 520, background: RCA.paper, borderLeft: `1px solid ${RCA.paper3}`,
        boxShadow: "-20px 0 40px rgba(20,22,28,.12)",
        zIndex: 61, display: "flex", flexDirection: "column",
        animation: "kbSlide 220ms cubic-bezier(.2,.7,.2,1)",
      }} className="rca">
        <style>{`
          @keyframes kbSlide { from { transform: translateX(100%); } to { transform: translateX(0); } }
          @keyframes kbFade { from { opacity: 0; } to { opacity: 1; } }
        `}</style>

        {/* header */}
        <div style={{ padding: "14px 18px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 32, height: 32, borderRadius: 6, background: RCA.ink, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <I name="sparkle" size={16} color={RCA.accent}/>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>Ask the knowledge base</div>
            <div style={{ fontSize: 11, color: RCA.textPaperD, display: "flex", alignItems: "center", gap: 8 }}>
              <span><span style={{ color: RCA.ok }}>●</span> {activeCols.length} collections · {totalDocs} docs in context</span>
              <span>·</span>
              <span onClick={onOpenKB} style={{ cursor: "pointer", textDecoration: "underline", textDecorationStyle: "dotted" }}>manage</span>
            </div>
          </div>
          <Btn size="sm" variant="ghost" icon={<I name="clock" size={13}/>} onClick={onOpenChats}>History</Btn>
          <Btn size="sm" variant="ghost" icon={<I name="x" size={14}/>} onClick={onClose}/>
        </div>

        {/* Collection picker */}
        <div style={{ padding: "12px 18px", borderBottom: `1px solid ${RCA.paper3}`, background: RCA.paper2 }}>
          <CapsLabel style={{ marginBottom: 8 }}>Search across</CapsLabel>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {KB_COLLECTIONS.map((c) => {
              const on = activeCols.includes(c.id);
              return (
                <div key={c.id} onClick={() => toggleCol(c.id)} style={{
                  padding: "5px 10px",
                  borderRadius: 14,
                  border: `1px solid ${on ? RCA.accent : RCA.paper3}`,
                  background: on ? RCA.accent : RCA.white,
                  color: on ? RCA.white : RCA.textPaper,
                  fontSize: 12,
                  cursor: "pointer",
                  display: "inline-flex", alignItems: "center", gap: 6,
                  whiteSpace: "nowrap",
                }}>
                  <I name={on ? "check" : "plus"} size={11} color={on ? RCA.white : RCA.textPaperD}/>
                  <span>{c.title}</span>
                  <span style={{ fontFamily: RCA.fMono, fontSize: 10, opacity: 0.75 }}>{c.docs}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* messages */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "18px 18px", display: "flex", flexDirection: "column", gap: 16 }}>
          {messages.map((m, i) => m.role === "user" ? (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Avatar name={m.who} size={20}/>
                <span style={{ fontSize: 12, fontWeight: 600 }}>You</span>
                <span style={{ fontSize: 11, color: RCA.textPaperD2 }}>{m.time}</span>
              </div>
              <div style={{ paddingLeft: 28, fontSize: 14, lineHeight: 1.55 }}>{m.text}</div>
            </div>
          ) : (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ width: 20, height: 20, borderRadius: 4, background: RCA.ink, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <RCAMark size={14} color={RCA.textDark}/>
                </div>
                <span style={{ fontSize: 12, fontWeight: 600 }}>KB Agent</span>
              </div>
              <div style={{ paddingLeft: 28, fontSize: 14, lineHeight: 1.55, color: RCA.textPaper }}>{m.text}</div>
              {m.citations && (
                <div style={{ paddingLeft: 28, marginTop: 6, display: "flex", flexDirection: "column", gap: 5 }}>
                  <CapsLabel style={{ marginBottom: 2, fontSize: 9 }}>Sources</CapsLabel>
                  {m.citations.map((c, j) => (
                    <div key={j} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6, cursor: "pointer" }}>
                      <span className="mono" style={{ fontSize: 10, color: RCA.accent, fontWeight: 700, minWidth: 22 }}>[{j+1}]</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, color: RCA.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {c.col} <span style={{ color: RCA.textPaperD2, fontFamily: RCA.fMono, fontSize: 11 }}>· {c.n} {c.n === 1 ? "chunk" : "chunks"}</span>
                        </div>
                        <div style={{ fontSize: 11, color: RCA.textPaperD, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.snippet}</div>
                      </div>
                      <I name="arrow_r" size={12} color={RCA.textPaperD2}/>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {pending && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 28, color: RCA.textPaperD2, fontFamily: RCA.fMono, fontSize: 12 }}>
              <span style={{ display: "inline-flex", gap: 2 }}>
                {[0,1,2].map(i=>(<span key={i} style={{ width: 4, height: 4, borderRadius: "50%", background: RCA.accent, opacity: 0.4 + i*0.2 }}/>))}
              </span>
              searching {activeCols.length} {activeCols.length === 1 ? "collection" : "collections"}…
            </div>
          )}
        </div>

        {/* suggestions + composer */}
        <div style={{ padding: "10px 18px 0", borderTop: `1px solid ${RCA.paper3}` }}>
          <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
            {KB_SUGGESTIONS.map((s, i) => (
              <div key={i} onClick={() => send(s)} style={{
                padding: "5px 10px", border: `1px solid ${RCA.paper3}`, borderRadius: 14,
                fontSize: 11, color: RCA.textPaper, background: RCA.white,
                cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5,
              }}>
                <I name="sparkle" size={11} color={RCA.accent}/>{s}
              </div>
            ))}
          </div>
        </div>
        <div style={{ padding: "0 18px 18px" }}>
          <div style={{ background: RCA.white, border: `1.5px solid ${RCA.accent}`, borderRadius: 8, padding: 12 }}>
            <div style={{ fontSize: 14, color: RCA.ink, marginBottom: 10 }}>
              What does IPC-A-610 say about void rate acceptance?
              <span style={{ width: 1.5, height: 16, background: RCA.accent, marginLeft: 2, display: "inline-block", verticalAlign: "middle" }}/>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Btn size="sm" variant="ghost" icon={<I name="plus" size={13}/>}>Attach</Btn>
              <div style={{ flex: 1 }}/>
              <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>⌘↵</span>
              <Btn size="sm" variant="primary" icon={<I name="arrow_r" size={13}/>} onClick={() => send("What does IPC-A-610 say about void rate acceptance?")}>Send</Btn>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ============================================================
// 2) KB PAGE — collections grid
// ============================================================
function KBPage({ onBack, onAskAgent }) {
  const W = 1440, H = 900;
  const [selectedCol, setSelectedCol] = React.useState(null);

  if (selectedCol) {
    return <CollectionPage W={W} H={H} collection={selectedCol} onBack={() => setSelectedCol(null)} onBackHome={onBack} onAskAgent={onAskAgent}/>;
  }

  return (
    <div className="rca" style={{ width: W, height: H, background: RCA.paper, display: "flex", overflow: "hidden", color: RCA.textPaper, position: "relative" }}>
      <KBSidebar active="kb" onBack={onBack}/>
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* top bar */}
        <div style={{ height: 64, padding: "0 28px", display: "flex", alignItems: "center", borderBottom: `1px solid ${RCA.paper3}`, gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 38, width: 420, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={15} color={RCA.textPaperD}/>
            <span style={{ color: RCA.textPaperD, fontSize: 13, flex: 1 }}>Search collections, documents, chunks…</span>
            <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD, padding: "2px 6px", border: `1px solid ${RCA.paper3}`, borderRadius: 4 }}>⌘K</span>
          </div>
          <div style={{ flex: 1 }}/>
          <Btn variant="ghost" icon={<I name="bell" size={15}/>}>3</Btn>
          <Btn icon={<I name="sparkle" size={14}/>} onClick={onAskAgent}>Ask agent</Btn>
        </div>

        {/* page header */}
        <div style={{ padding: "28px 28px 20px", display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24 }}>
          <div>
            <CapsLabel style={{ marginBottom: 10 }}>Knowledge base</CapsLabel>
            <h1 className="display" style={{ fontSize: 40 }}>
              {KB_COLLECTIONS.length} collections <span style={{ color: RCA.accent }}>·</span> {KB_COLLECTIONS.reduce((s, c) => s + c.docs, 0)} documents
            </h1>
            <p style={{ color: RCA.textPaperD, fontSize: 14, marginTop: 8 }}>Collections are the unit of search. Pick which to use as context when chatting.</p>
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <KBMetric label="My collections" value="3" sub="plus 5 shared"/>
            <KBMetric label="Total size" value="595 MB" sub="≈ 12.4 M tokens"/>
            <KBMetric label="Most cited" value="Past invs." sub="38 citations · 7d"/>
          </div>
        </div>

        {/* tabs */}
        <div style={{ padding: "0 28px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "stretch", gap: 28 }}>
          {[
            ["All", KB_COLLECTIONS.length, true],
            ["Mine", 3],
            ["Shared with me", 5],
            ["Pinned", 2],
            ["Auto", 1],
          ].map(([t, c, act], i) => (
            <div key={i} style={{ padding: "12px 0", borderBottom: act ? `2px solid ${RCA.accent}` : `2px solid transparent`, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <span style={{ fontSize: 14, fontWeight: act ? 600 : 400, color: act ? RCA.ink : RCA.textPaperD }}>{t}</span>
              <span className="mono" style={{ fontSize: 11, color: act ? RCA.accent : RCA.textPaperD2 }}>{c}</span>
            </div>
          ))}
        </div>

        {/* action strip */}
        <div style={{ padding: "16px 28px", display: "flex", gap: 8, alignItems: "center" }}>
          <Btn size="sm" icon={<I name="filter" size={13}/>}>Filter</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12}/>}>Owner · any</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12}/>}>Shared with · any</Btn>
          <div style={{ flex: 1 }}/>
          <Btn size="sm" variant="primary" icon={<I name="plus" size={13}/>}>New collection</Btn>
        </div>

        {/* collection grid */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "0 28px 28px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
            {KB_COLLECTIONS.map((c) => (
              <div key={c.id} onClick={() => setSelectedCol(c)} style={{
                padding: 16,
                background: RCA.white,
                border: `1px solid ${RCA.paper3}`,
                borderRadius: 8,
                cursor: "pointer",
                display: "flex", flexDirection: "column", gap: 10,
                position: "relative",
              }}>
                {c.pinned && (
                  <div style={{ position: "absolute", top: 12, right: 12, color: RCA.accent }}>
                    <I name="pin" size={14}/>
                  </div>
                )}
                <div style={{ width: 36, height: 36, borderRadius: 8, background: c.auto ? RCA.ink : RCA.accentSoft, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <I name={c.icon || (c.auto ? "sparkle" : "layers")} size={18} color={c.auto ? RCA.accent : RCA.accentH}/>
                </div>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: RCA.ink, marginBottom: 4 }}>{c.title}</div>
                  <div style={{ fontSize: 12, color: RCA.textPaperD, lineHeight: 1.5, minHeight: 32 }}>{c.desc}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
                  <RcaChip tone="outline"><I name="file" size={10}/> {c.docs} docs</RcaChip>
                  <RcaChip tone="outline">{c.size}</RcaChip>
                  {(c.cited ?? 0) > 0 && (
                    <RcaChip tone="accent" icon={<I name="chat" size={10}/>}>cited {c.cited}×</RcaChip>
                  )}
                  {c.auto && <RcaChip tone="default" icon={<I name="sparkle" size={10}/>}>auto</RcaChip>}
                </div>
                <div style={{ borderTop: `1px solid ${RCA.paper3}`, paddingTop: 10, display: "flex", alignItems: "center", gap: 8 }}>
                  <Avatar name={c.owner.initials} size={20}/>
                  <span style={{ fontSize: 12, color: RCA.textPaperD }}>{c.owner.name}</span>
                  <span style={{ fontSize: 11, color: RCA.textPaperD2, fontFamily: RCA.fMono }}>· {c.shared}</span>
                  <div style={{ flex: 1 }}/>
                  <span style={{ fontSize: 11, color: RCA.textPaperD2, fontFamily: RCA.fMono }}>{c.updated}</span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 16, fontSize: 12, color: RCA.textPaperD, display: "flex", alignItems: "center", gap: 6 }}>
            <I name="sparkle" size={12} color={RCA.accent}/>
            The <strong style={{ color: RCA.ink }}>Past investigations</strong> collection is the only auto-managed one — closed investigations are added to it automatically.
          </div>
        </div>
      </main>
    </div>
  );
}

// ============================================================
// COLLECTION PAGE — a full page, not a drawer
// ============================================================
function CollectionPage({ W, H, collection, onBack, onBackHome, onAskAgent }) {
  const isAuto = collection.auto;
  const [iconPickerOpen, setIconPickerOpen] = React.useState(false);
  const [chosenIcon, setChosenIcon] = React.useState(collection.icon || (isAuto ? "sparkle" : "layers"));
  const [pinned, setPinned] = React.useState(!!collection.pinned);
  const ICON_OPTIONS = ["layers", "file", "folder", "flame", "star", "sparkle", "check", "bug", "chart", "table", "photo", "globe", "settings", "users", "tag", "data"];
  const [selectedDoc, setSelectedDoc] = React.useState(null);
  const DOCS = isAuto ? [
    { title: "INC-0119 · Reflow zone-3 drift on MX-7",      updated: "2025-12-04", chunks: 14, kind: "investigation", size: "—", by: "auto", cited: 38 },
    { title: "INC-0098 · MX-5 zone-3 drift, ambient creep", updated: "2025-09-21", chunks: 11, kind: "investigation", size: "—", by: "auto", cited: 22 },
    { title: "INC-0072 · MX-7 voids, humidity contributor", updated: "2025-06-12", chunks: 19, kind: "investigation", size: "—", by: "auto", cited: 14 },
    { title: "INC-0064 · Wirebond pull spec deviation",     updated: "2025-05-08", chunks: 8,  kind: "investigation", size: "—", by: "auto", cited: 9 },
    { title: "INC-0058 · Underfill voids on Module N4",     updated: "2025-04-22", chunks: 12, kind: "investigation", size: "—", by: "auto", cited: 6 },
    { title: "INC-0042 · Paint ΔE shift after lot B-24-1209", updated: "2024-12-18", chunks: 7, kind: "investigation", size: "—", by: "auto", cited: 2 },
  ] : [
    { title: "reflow-pid-tuning.pdf",       updated: "2025-08-12", chunks: 22, kind: "pdf",   size: "2.1 MB", by: "Alice C.", cited: 18 },
    { title: "zone-profile-baseline.xlsx",  updated: "2025-08-10", chunks: 4,  kind: "sheet", size: "68 KB",  by: "Alice C.", cited: 11 },
    { title: "change-log-2025-q3.md",       updated: "2025-08-08", chunks: 18, kind: "md",    size: "42 KB",  by: "Bob L.",   cited: 8 },
    { title: "DOE-throughput-vs-temp.docx", updated: "2025-07-29", chunks: 9,  kind: "doc",   size: "480 KB", by: "Bob L.",   cited: 4 },
    { title: "shift-handover-template.md",  updated: "2025-07-12", chunks: 3,  kind: "md",    size: "6 KB",   by: "Alice C.", cited: 0 },
    { title: "pid-gain-charts.png",         updated: "2025-07-04", chunks: 1,  kind: "image", size: "840 KB", by: "Alice C.", cited: 1 },
  ];

  return (
    <div className="rca" style={{ width: W, height: H, background: RCA.paper, display: "flex", overflow: "hidden", color: RCA.textPaper, position: "relative" }}>
      <KBSidebar active="kb" onBack={onBackHome}/>
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* top bar */}
        <div style={{ height: 64, padding: "0 28px", display: "flex", alignItems: "center", borderBottom: `1px solid ${RCA.paper3}`, gap: 16 }}>
          <Btn size="sm" variant="ghost" icon={<I name="chev_l" size={13}/>} onClick={onBack}>Knowledge base</Btn>
          <div style={{ flex: 1 }}/>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 38, width: 320, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={14} color={RCA.textPaperD}/>
            <span style={{ color: RCA.textPaperD, fontSize: 12, flex: 1 }}>Search in this collection…</span>
          </div>
          <Btn variant="ghost" icon={<I name="bell" size={15}/>}>3</Btn>
          <Btn icon={<I name="sparkle" size={14}/>} onClick={onAskAgent}>Ask agent</Btn>
        </div>

        {/* breadcrumb + header */}
        <div style={{ padding: "22px 28px 18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 14, fontSize: 12, color: RCA.textPaperD }}>
            <span onClick={onBack} style={{ cursor: "pointer" }}>Knowledge base</span>
            <I name="chev_r" size={10} color={RCA.textPaperD2}/>
            <span style={{ color: RCA.ink, fontWeight: 600 }}>{collection.title}</span>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24 }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
              <div onClick={() => !isAuto && setIconPickerOpen((v) => !v)} title={isAuto ? "" : "Click to change icon"} style={{ position: "relative", width: 56, height: 56, borderRadius: 10, background: isAuto ? RCA.ink : RCA.accentSoft, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, cursor: isAuto ? "default" : "pointer" }}>
                <I name={chosenIcon} size={26} color={isAuto ? RCA.accent : RCA.accentH}/>
                {!isAuto && (
                  <span style={{ position: "absolute", bottom: -2, right: -2, width: 18, height: 18, borderRadius: 9, background: RCA.ink, color: RCA.white, display: "flex", alignItems: "center", justifyContent: "center", border: `2px solid ${RCA.paper}` }}>
                    <I name="plus" size={10} color={RCA.white}/>
                  </span>
                )}
                {iconPickerOpen && (
                  <div onClick={(e) => e.stopPropagation()} style={{ position: "absolute", top: 64, left: 0, zIndex: 30, padding: 12, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, boxShadow: "0 10px 30px rgba(20,22,28,.12)", width: 280 }}>
                    <CapsLabel style={{ marginBottom: 8 }}>Choose an icon</CapsLabel>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gap: 6 }}>
                      {ICON_OPTIONS.map((n) => {
                        const on = chosenIcon === n;
                        return (
                          <div key={n} onClick={() => { setChosenIcon(n); setIconPickerOpen(false); }} style={{
                            width: 28, height: 28, borderRadius: 6,
                            background: on ? RCA.accent : "transparent",
                            border: `1px solid ${on ? RCA.accent : RCA.paper3}`,
                            display: "flex", alignItems: "center", justifyContent: "center",
                            cursor: "pointer",
                          }}>
                            <I name={n} size={14} color={on ? RCA.white : RCA.ink2}/>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                  <RcaChip tone="outline">{collection.shared}</RcaChip>
                  {isAuto && <RcaChip tone="default" icon={<I name="sparkle" size={10}/>}>auto-managed</RcaChip>}
                  {pinned ? (
                    <span onClick={() => setPinned(false)} title="Click to unpin" style={{ cursor: "pointer" }}>
                      <RcaChip tone="accent" icon={<I name="pin" size={10}/>}>pinned · unpin</RcaChip>
                    </span>
                  ) : (
                    <span onClick={() => setPinned(true)} title="Click to pin" style={{ cursor: "pointer" }}>
                      <RcaChip tone="outline" icon={<I name="pin" size={10}/>}>pin</RcaChip>
                    </span>
                  )}
                </div>
                <h1 className="display" style={{ fontSize: 32 }}>{collection.title}</h1>
                <p style={{ fontSize: 14, color: RCA.textPaperD, margin: 0, marginTop: 6, maxWidth: 560, lineHeight: 1.5 }}>{collection.desc}</p>
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <Btn size="sm" variant="ghost" icon={<I name="download" size={13}/>}>Export</Btn>
              <Btn size="sm" variant="ghost" icon={<I name="users" size={13}/>}>Share</Btn>
              <Btn size="sm" variant="ghost" icon={<I name="settings" size={13}/>}/>
              {!isAuto && <Btn size="sm" variant="primary" icon={<I name="upload" size={13}/>} iconRight={<I name="chev_d" size={11}/>}>Upload</Btn>}
            </div>
          </div>
        </div>

        {/* meta strip */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr 1fr 1fr", borderTop: `1px solid ${RCA.paper3}`, borderBottom: `1px solid ${RCA.paper3}`, background: RCA.paper2 }}>
          {[
            ["Documents", collection.docs.toLocaleString()],
            ["Size", collection.size],
            ["Chunks", Math.round(collection.docs * 8).toLocaleString()],
            ["Cited", (collection.cited ?? 0) + "×", (collection.cited ?? 0) > 0],
            ["Owner", collection.owner.name],
            ["Updated", collection.updated],
          ].map(([k, v, hot], i) => (
            <div key={i} style={{ padding: "14px 18px", borderRight: i < 5 ? `1px solid ${RCA.paper3}` : "none" }}>
              <CapsLabel style={{ marginBottom: 4, fontSize: 9 }}>{k}</CapsLabel>
              <div style={{ fontSize: 14, fontWeight: 600, color: hot ? RCA.accent : RCA.ink }}>{v}</div>
            </div>
          ))}
        </div>

        {/* tabs */}
        <div style={{ padding: "0 28px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "stretch", gap: 28 }}>
          {[["Documents", DOCS.length, true], ["Activity"], ["Permissions"]].map(([t, c, act], i) => (
            <div key={i} style={{ padding: "12px 0", borderBottom: act ? `2px solid ${RCA.accent}` : `2px solid transparent`, display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
              <span style={{ fontSize: 14, fontWeight: act ? 600 : 400, color: act ? RCA.ink : RCA.textPaperD }}>{t}</span>
              {c != null && <span className="mono" style={{ fontSize: 11, color: act ? RCA.accent : RCA.textPaperD2 }}>{c.toLocaleString()}</span>}
            </div>
          ))}
        </div>

        {/* action / filter strip */}
        <div style={{ padding: "14px 28px", display: "flex", gap: 8, alignItems: "center" }}>
          <Btn size="sm" icon={<I name="filter" size={13}/>}>Filter</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12}/>}>Type · any</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12}/>}>Uploader · any</Btn>
          <div style={{ flex: 1 }}/>
          <span style={{ fontSize: 12, color: RCA.textPaperD }}>Sort: <strong style={{ color: RCA.ink }}>Newest</strong></span>
        </div>

        {/* docs list + (right) optional upload zone */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "0 28px 28px" }}>
          <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, overflow: "hidden" }}>
            {/* head */}
            <div style={{ display: "grid", gridTemplateColumns: "36px 2.4fr 1fr 0.9fr 0.7fr 0.7fr 0.7fr 32px", padding: "10px 16px", borderBottom: `1px solid ${RCA.paper3}`, alignItems: "center", gap: 12 }}>
              <div></div>
              {["Name", "Uploaded by", "Updated", "Size", "Chunks", "Cited"].map((h, i) => (
                <div key={i} className="caps" style={{ fontSize: 10, color: RCA.textPaperD }}>{h}</div>
              ))}
              <div></div>
            </div>
            {DOCS.map((d, i) => (
              <div key={i} onClick={() => setSelectedDoc(d)} style={{ display: "grid", gridTemplateColumns: "36px 2.4fr 1fr 0.9fr 0.7fr 0.7fr 0.7fr 32px", padding: "12px 16px", borderBottom: i < DOCS.length - 1 ? `1px solid ${RCA.paper3}` : "none", alignItems: "center", gap: 12, cursor: "pointer" }}>
                <div style={{ width: 30, height: 30, borderRadius: 5, background: RCA.paper2, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <I name={d.kind === "investigation" ? "bug" : d.kind === "sheet" ? "table" : d.kind === "image" ? "photo" : "file"} size={14} color={RCA.ink2}/>
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: RCA.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.title}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: RCA.textPaperD }}>
                  {d.by !== "auto" && <Avatar name={d.by.split(" ")[0].slice(0,2)} size={20}/>}
                  {d.by}
                </div>
                <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD }}>{d.updated}</span>
                <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>{d.size}</span>
                <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>{d.chunks}</span>
                <span className="mono" style={{ fontSize: 12, color: (d.cited ?? 0) === 0 ? RCA.textPaperD2 : RCA.accent, fontWeight: (d.cited ?? 0) > 0 ? 700 : 400 }}>{d.cited ?? 0}</span>
                <span title="Doc actions" style={{ color: RCA.textPaperD2, display: "flex", justifyContent: "center" }}>
                  <I name="dots_v" size={14}/>
                </span>
              </div>
            ))}
          </div>

          {!isAuto && (
            <div style={{ marginTop: 14, padding: "22px 18px", background: "transparent", border: `2px dashed ${RCA.paper3}`, borderRadius: 8, display: "flex", flexDirection: "column", alignItems: "center", gap: 8, cursor: "pointer" }}>
              <I name="upload" size={20} color={RCA.textPaperD}/>
              <div style={{ fontSize: 13, color: RCA.textPaper, fontWeight: 500 }}>Drop files or a folder to add to this collection</div>
              <div style={{ fontSize: 11, color: RCA.textPaperD2 }}>PDF · DOCX · MD · TXT · CSV · images — up to 50 MB each. Folders preserve their tree as tags.</div>
              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                <Btn size="sm" variant="ghost" icon={<I name="file" size={12}/>}>Choose files</Btn>
                <Btn size="sm" variant="ghost" icon={<I name="folder" size={12}/>}>Choose folder</Btn>
              </div>
            </div>
          )}
        </div>
      </main>

      {selectedDoc && <DocPreviewDrawer doc={selectedDoc} collectionTitle={collection.title} onClose={() => setSelectedDoc(null)}/>}
    </div>
  );
}

// ============================================================
// DOC PREVIEW — drawer (peek, not deep). Headline + chunks list.
// ============================================================
function DocPreviewDrawer({ doc, collectionTitle, onClose }) {
  return (
    <>
      <div onClick={onClose} style={{
        position: "absolute", inset: 0,
        background: "rgba(20,22,28,0.25)",
        zIndex: 40,
      }}/>
      <div className="rca" style={{
        position: "absolute", top: 0, right: 0, bottom: 0,
        width: 620, background: RCA.paper,
        borderLeft: `1px solid ${RCA.paper3}`,
        boxShadow: "-20px 0 40px rgba(20,22,28,.12)",
        zIndex: 41, display: "flex", flexDirection: "column",
        animation: "kbSlide 220ms cubic-bezier(.2,.7,.2,1)",
      }}>
        <div style={{ padding: "16px 20px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div style={{ width: 38, height: 38, borderRadius: 6, background: RCA.paper2, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
            <I name={doc.kind === "investigation" ? "bug" : doc.kind === "sheet" ? "table" : doc.kind === "image" ? "photo" : "file"} size={18} color={RCA.ink2}/>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, color: RCA.textPaperD, marginBottom: 2 }}>{collectionTitle}</div>
            <h3 className="display" style={{ fontSize: 18 }}>{doc.title}</h3>
            <div style={{ display: "flex", gap: 12, marginTop: 4, fontSize: 11, color: RCA.textPaperD, fontFamily: RCA.fMono }}>
              <span>{doc.size}</span>
              <span>·</span>
              <span style={{ color: (doc.cited ?? 0) > 0 ? RCA.accent : RCA.textPaperD2 }}>cited {doc.cited ?? 0}×</span>
              <span>·</span>
              <span>{doc.chunks} chunks</span>
              <span>·</span>
              <span>uploaded {doc.updated}</span>
            </div>
          </div>
          <Btn size="sm" variant="ghost" icon={<I name="x" size={14}/>} onClick={onClose}/>
        </div>

        <div style={{ padding: "10px 20px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", gap: 6 }}>
          <Btn size="sm" icon={<I name="eye" size={13}/>}>Open full view</Btn>
          <Btn size="sm" variant="ghost" icon={<I name="download" size={13}/>}>Download</Btn>
          <div style={{ flex: 1 }}/>
          <Btn size="sm" variant="ghost" icon={<I name="play" size={13}/>}>Re-index</Btn>
          <Btn size="sm" variant="ghost" icon={<I name="x" size={13}/>}>Remove</Btn>
        </div>

        {/* FILE PREVIEW */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "20px", background: RCA.paper2 }}>
          <DocPreviewBody doc={doc}/>
        </div>
      </div>
    </>
  );
}

function DocPreviewBody({ doc }) {
  const kind = doc.kind;

  if (kind === "pdf") {
    return (
      <div style={{ background: "#fff", border: `1px solid ${RCA.paper3}`, boxShadow: "0 2px 12px rgba(20,22,28,.04)", padding: "40px 44px", borderRadius: 4, minHeight: 700 }}>
        <div style={{ fontFamily: "Georgia, serif", fontSize: 11, color: RCA.textPaperD2, marginBottom: 28, display: "flex", justifyContent: "space-between" }}>
          <span>Reflow zone-3 · PID tuning guide</span>
          <span>p. 4 / 18</span>
        </div>
        <h1 style={{ fontFamily: "Georgia, serif", fontSize: 22, fontWeight: 700, marginBottom: 16, color: RCA.ink }}>4 · Re-tuning procedure</h1>
        <p style={{ fontFamily: "Georgia, serif", fontSize: 14, lineHeight: 1.65, color: RCA.ink, marginBottom: 14 }}>
          Reflow zone-3 PID gains shall be re-tuned whenever throughput aggregation exceeds 8% of nominal over any 24-hour window. Use a Ziegler-Nichols variant on the affected zone with the closed-loop ultimate-gain method.
        </p>
        <p style={{ fontFamily: "Georgia, serif", fontSize: 14, lineHeight: 1.65, color: RCA.ink, marginBottom: 14 }}>
          Acceptance criterion: zone-3 actual temperature shall remain within ±1°C of set-point across the heat-soak phase (zones 2–3, 38–52 s). If the heat-soak phase exhibits oscillation greater than 0.5 Hz, reduce the derivative gain in 10% steps until oscillation subsides.
        </p>
        <h2 style={{ fontFamily: "Georgia, serif", fontSize: 17, fontWeight: 700, marginTop: 22, marginBottom: 10, color: RCA.ink }}>4.1 · Pre-conditions</h2>
        <ul style={{ fontFamily: "Georgia, serif", fontSize: 14, lineHeight: 1.65, color: RCA.ink, paddingLeft: 22, marginBottom: 14 }}>
          <li>Oven warmed for ≥ 60 min at target profile.</li>
          <li>Board population density logged in change-control matrix.</li>
          <li>Conveyor speed measured against drift sensor on rail 2.</li>
        </ul>
        <h2 style={{ fontFamily: "Georgia, serif", fontSize: 17, fontWeight: 700, marginTop: 22, marginBottom: 10, color: RCA.ink }}>4.2 · Procedure</h2>
        <ol style={{ fontFamily: "Georgia, serif", fontSize: 14, lineHeight: 1.65, color: RCA.ink, paddingLeft: 22 }}>
          <li>Disable autotune. Set Ki and Kd to 0.</li>
          <li>Increase Kp until the system oscillates with constant amplitude — record this as Kᵤ.</li>
          <li>Measure oscillation period Tᵤ across 5 cycles; use the mean.</li>
          <li>Compute final gains: Kp = 0.6 · Kᵤ, Ki = 1.2 · Kᵤ / Tᵤ, Kd = 0.075 · Kᵤ · Tᵤ.</li>
        </ol>
      </div>
    );
  }

  if (kind === "sheet") {
    const rows = [
      ["Zone", "Set (°C)", "Soak (s)", "Ramp", "Notes"],
      ["1", "120", "30", "1.5°C/s", "preheat"],
      ["2", "180", "40", "1.0°C/s", "preheat→soak"],
      ["3", "245", "52", "0.7°C/s", "soak / reflow"],
      ["4", "245", "20", "0.0°C/s", "reflow plateau"],
      ["5", "120", "30", "−2.0°C/s", "cool"],
    ];
    return (
      <div style={{ background: "#fff", border: `1px solid ${RCA.paper3}`, borderRadius: 4 }}>
        <div style={{ padding: "10px 14px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontFamily: RCA.fMono, fontSize: 12, color: RCA.textPaperD }}>Sheet 1 — baseline</span>
          <span style={{ fontFamily: RCA.fMono, fontSize: 11, color: RCA.textPaperD2 }}>· 5 rows · 5 cols</span>
        </div>
        <div style={{ overflow: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontFamily: RCA.fMono, fontSize: 12 }}>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ background: i === 0 ? RCA.paper2 : "transparent" }}>
                  {r.map((cell, j) => (
                    <td key={j} style={{ padding: "8px 12px", border: `1px solid ${RCA.paper3}`, fontWeight: i === 0 ? 600 : 400, color: i === 0 ? RCA.textPaperD : RCA.ink }}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (kind === "md") {
    return (
      <div style={{ background: "#fff", border: `1px solid ${RCA.paper3}`, padding: "32px 36px", borderRadius: 4, color: RCA.ink, fontFamily: RCA.fBody, fontSize: 14, lineHeight: 1.65 }}>
        <h1 style={{ fontFamily: RCA.fSans, fontSize: 26, fontWeight: 700, marginBottom: 14, letterSpacing: "-0.02em" }}>Change log · 2025 Q3</h1>
        <h2 style={{ fontFamily: RCA.fSans, fontSize: 18, fontWeight: 600, marginTop: 20, marginBottom: 8 }}>2025-08-12 · Reflow zone-3 PID retune</h2>
        <p style={{ marginBottom: 10 }}>Retuned after observing 3.2°C drift on MX-7. New Kp = 1.8, Ki = 0.4, Kd = 0.08. See <span style={{ fontFamily: RCA.fMono, color: RCA.accent }}>INC-0119</span> for full root-cause analysis.</p>
        <h2 style={{ fontFamily: RCA.fSans, fontSize: 18, fontWeight: 600, marginTop: 20, marginBottom: 8 }}>2025-08-04 · AOI threshold review</h2>
        <p style={{ marginBottom: 10 }}>Lowered void-rate alarm threshold from 2.5% to 2.0% in line with updated IPC-A-610 guidance. Rolling 7-day baseline now baseline-anchored rather than absolute.</p>
        <h2 style={{ fontFamily: RCA.fSans, fontSize: 18, fontWeight: 600, marginTop: 20, marginBottom: 8 }}>2025-07-22 · Change-control matrix update</h2>
        <p style={{ marginBottom: 10 }}>Added <strong>throughput aggregation</strong> as a tracked process change. Triggers PID retune when aggregated throughput exceeds 8% of nominal over any 24-hour window.</p>
        <ul style={{ paddingLeft: 22, marginTop: 6 }}>
          <li>Owner: Process eng on-call</li>
          <li>SPC: zone-actual vs set-point Δ &gt; 2°C / 15 min</li>
        </ul>
      </div>
    );
  }

  if (kind === "image") {
    return (
      <div style={{ background: "#fff", border: `1px solid ${RCA.paper3}`, borderRadius: 4, padding: 18 }}>
        <Hatch2 h={340} label="pid-gain-charts.png · 1640 × 920"/>
        <div style={{ marginTop: 10, padding: "8px 12px", background: RCA.paper2, borderRadius: 4, fontSize: 12, color: RCA.textPaperD }}>
          <strong style={{ color: RCA.ink, fontFamily: RCA.fBody }}>Caption:</strong> Pre/post-tune gain comparison · reflow zone-3 · 14d window
        </div>
      </div>
    );
  }

  if (kind === "investigation") {
    return (
      <div style={{ background: "#fff", border: `1px solid ${RCA.paper3}`, padding: "28px 32px", borderRadius: 4 }}>
        <div style={{ fontFamily: RCA.fMono, fontSize: 11, color: RCA.accent, marginBottom: 4, letterSpacing: "0.08em" }}>INVESTIGATION · RESOLVED</div>
        <h1 style={{ fontFamily: RCA.fSans, fontSize: 22, fontWeight: 700, marginBottom: 4, letterSpacing: "-0.02em", color: RCA.ink }}>Reflow zone-3 drift on MX-7</h1>
        <div style={{ fontSize: 12, color: RCA.textPaperD, marginBottom: 20 }}>Owner: Alice Chen · Severity: P1 · Opened 2025-12-04 · Resolved 2025-12-08</div>

        {[
          ["D2 · Problem", "Void rate climbed 1.4% → 3.2% on MX-7, Line 3, 2025-12-04 14:00. Sustained across 4 shifts."],
          ["D4 · Root cause", "Reflow zone-3 PID gains, tuned for prior throughput profile, failed to maintain 245°C set-point under increased throughput. The throughput change was not flagged by change-control."],
          ["D7 · Preventive", "Added throughput aggregation > 8% as change-control trigger. Added SPC alarm for zone-actual vs set-point delta > 2°C over 15 min window."],
        ].map(([k, v], i) => (
          <div key={i} style={{ marginBottom: 14 }}>
            <div className="caps" style={{ fontSize: 10, color: RCA.accent, marginBottom: 4 }}>{k}</div>
            <p style={{ fontSize: 14, lineHeight: 1.6, color: RCA.ink, margin: 0 }}>{v}</p>
          </div>
        ))}
      </div>
    );
  }

  // docx / default
  return (
    <div style={{ background: "#fff", border: `1px solid ${RCA.paper3}`, padding: "40px 48px", borderRadius: 4, minHeight: 600, fontFamily: "Georgia, serif", color: RCA.ink, fontSize: 14, lineHeight: 1.7 }}>
      <h1 style={{ fontFamily: "Georgia, serif", fontSize: 22, fontWeight: 700, marginBottom: 18 }}>DOE — Throughput vs Reflow Temperature</h1>
      <p style={{ marginBottom: 14 }}>This design of experiments tests how board throughput per minute correlates with measured reflow zone-3 temperature, with controlled variation across 3 levels of throughput and 2 paste lots.</p>
      <h2 style={{ fontFamily: "Georgia, serif", fontSize: 17, fontWeight: 700, marginTop: 18, marginBottom: 8 }}>Objective</h2>
      <p style={{ marginBottom: 14 }}>Quantify the response of zone-3 actual temperature to throughput, holding paste and ambient humidity constant.</p>
      <h2 style={{ fontFamily: "Georgia, serif", fontSize: 17, fontWeight: 700, marginTop: 18, marginBottom: 8 }}>Factors</h2>
      <ul style={{ paddingLeft: 22 }}>
        <li>Throughput: 40, 55, 70 boards/min</li>
        <li>Paste lot: PA-25-W12, PA-25-W14</li>
        <li>Ambient RH: 45 ± 3%</li>
      </ul>
    </div>
  );
}

function KBMetric({ label, value, sub }) {
  return (
    <div style={{ minWidth: 130 }}>
      <CapsLabel style={{ marginBottom: 6 }}>{label}</CapsLabel>
      <div className="display" style={{ fontSize: 24 }}>{value}</div>
      <div style={{ fontSize: 11, color: RCA.textPaperD2, marginTop: 2 }}>{sub}</div>
    </div>
  );
}

// ============================================================
// 3) CHATS PAGE
// ============================================================
function ChatsPage({ onBack, onOpenChat, onAskAgent }) {
  const W = 1440, H = 900;
  const [selected, setSelected] = React.useState(CHAT_HISTORY[0].id);
  const chat = CHAT_HISTORY.find((c) => c.id === selected) || CHAT_HISTORY[0];

  return (
    <div className="rca" style={{ width: W, height: H, background: RCA.paper, display: "flex", overflow: "hidden", color: RCA.textPaper }}>
      <KBSidebar active="chats" onBack={onBack}/>
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ height: 64, padding: "0 28px", display: "flex", alignItems: "center", borderBottom: `1px solid ${RCA.paper3}`, gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 38, width: 420, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={15} color={RCA.textPaperD}/>
            <span style={{ color: RCA.textPaperD, fontSize: 13, flex: 1 }}>Search chats by title, content, citation…</span>
          </div>
          <div style={{ flex: 1 }}/>
          <Btn variant="ghost" icon={<I name="bell" size={15}/>}>3</Btn>
          <Btn icon={<I name="sparkle" size={14}/>} onClick={onAskAgent}>New chat</Btn>
        </div>

        <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
          <div style={{ width: 460, borderRight: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "20px 22px 8px" }}>
              <CapsLabel style={{ marginBottom: 8 }}>Conversations</CapsLabel>
              <h1 className="display" style={{ fontSize: 26 }}>
                {CHAT_HISTORY.length} chats <span style={{ color: RCA.accent }}>·</span> private to you
              </h1>
            </div>
            <div style={{ padding: "8px 18px", display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[["All", true], ["Pinned"], ["Shared with me"]].map(([t, act], i) => (
                <div key={i} style={{
                  padding: "4px 10px", borderRadius: 14, fontSize: 12,
                  border: `1px solid ${act ? RCA.accent : RCA.paper3}`,
                  background: act ? RCA.accentSoft : "transparent",
                  color: act ? RCA.accentH : RCA.textPaper,
                  cursor: "pointer",
                }}>{t}</div>
              ))}
            </div>
            <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "8px 10px 18px", display: "flex", flexDirection: "column", gap: 4 }}>
              {CHAT_HISTORY.map((c) => (
                <div key={c.id} onClick={() => setSelected(c.id)} style={{
                  padding: "10px 12px",
                  background: selected === c.id ? RCA.accentSoft + "88" : "transparent",
                  border: selected === c.id ? `1px solid ${RCA.accent}` : `1px solid transparent`,
                  borderRadius: 6,
                  cursor: "pointer",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                    {c.pinned && <I name="pin" size={11} color={RCA.accent}/>}
                    <span style={{ fontSize: 13, fontWeight: 600, color: RCA.ink, flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.title}</span>
                    <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD2 }}>{c.updated}</span>
                  </div>
                  {c.snippet && <div style={{ fontSize: 12, color: RCA.textPaperD, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.snippet}</div>}
                  <div style={{ display: "flex", gap: 5, marginTop: 6, alignItems: "center" }}>
                    <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>{c.msgs} msgs</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "20px 28px 16px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <RcaChip tone="default" icon={<I name="lock" size={10}/>}>private chat</RcaChip>
                  {chat.pinned && <RcaChip tone="accent" icon={<I name="pin" size={10}/>}>pinned</RcaChip>}
                </div>
                <h2 className="display" style={{ fontSize: 22, marginBottom: 4 }}>{chat.title}</h2>
                <div style={{ fontSize: 12, color: RCA.textPaperD, fontFamily: RCA.fMono }}>{chat.msgs} messages · updated {chat.updated} · started by Alice</div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <Btn size="sm" variant="ghost" icon={<I name="pin" size={13}/>}/>
                <Btn size="sm" variant="ghost" icon={<I name="download" size={13}/>}>Export</Btn>
                <Btn size="sm" variant="ghost" icon={<I name="users" size={13}/>}>Share</Btn>
              </div>
            </div>

            <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "20px 28px", display: "flex", flexDirection: "column", gap: 18, maxWidth: 760 }}>
              <ChatMsgUser who="AC" time="08-16 14:22">
                Has reflow zone-3 drift been seen before? Looking for similar cases on MX-7 or related boards.
              </ChatMsgUser>
              <ChatMsgAgent>
                <p style={{ margin: 0, marginBottom: 10 }}>
                  Yes — there are <strong>3 prior investigations</strong> where reflow zone-3 drift contributed to a void-rate spike. All three resolved with PID re-tune; none required hardware change.
                </p>
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 14, lineHeight: 1.7, color: RCA.textPaper }}>
                  <li><strong>INC-0119</strong> (2025-12) — MX-7, identical pattern; root in change-control matrix gap.</li>
                  <li><strong>INC-0098</strong> (2025-09) — MX-5, 4.1°C drift, baseline crept up first.</li>
                  <li><strong>INC-0072</strong> (2025-06) — MX-7, ambient humidity contributed.</li>
                </ul>
                <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 10 }}>
                  <CapsLabel style={{ marginBottom: 2, fontSize: 9 }}>Sources</CapsLabel>
                  {[
                    { col: "Past investigations",   n: 3, snippet: "INC-0119 · INC-0098 · INC-0072" },
                    { col: "Reflow process notes",  n: 1, snippet: "PID tuning runbook · zone-3 ± 1°C envelope" },
                  ].map((c, j) => (
                    <div key={j} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                      <span className="mono" style={{ fontSize: 10, color: RCA.accent, fontWeight: 700, minWidth: 22 }}>[{j+1}]</span>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 12 }}>{c.col} <span style={{ color: RCA.textPaperD2, fontFamily: RCA.fMono, fontSize: 11 }}>· {c.n}</span></div>
                        <div style={{ fontSize: 11, color: RCA.textPaperD }}>{c.snippet}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </ChatMsgAgent>
              <ChatMsgUser who="AC" time="08-16 14:24">
                Which one is most similar to the current spike (08-14 14:00)?
              </ChatMsgUser>
              <ChatMsgAgent>
                <strong>INC-0119</strong> — identical MX-7 board, drift of 3.0°C (vs your 3.2°C), spike following 8h shift change. Root: throughput aggregation not flagged by change-control. Containment + corrective ran in 4 days.
              </ChatMsgAgent>
              <div style={{ paddingLeft: 28, color: RCA.textPaperD2, fontSize: 11, fontFamily: RCA.fMono }}>· {chat.msgs - 4} earlier messages collapsed</div>
            </div>

            {/* inline composer — continue the chat right here */}
            <div style={{ padding: "12px 28px 18px", borderTop: `1px solid ${RCA.paper3}`, background: RCA.paper }}>
              <div style={{ background: RCA.white, border: `1.5px solid ${RCA.accent}`, borderRadius: 8, padding: 12, maxWidth: 760 }}>
                <div style={{ fontSize: 14, color: RCA.textPaperD2, marginBottom: 10 }}>Reply to continue this chat…</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <Btn size="sm" variant="ghost" icon={<I name="plus" size={13}/>}>Attach</Btn>
                  <RcaChip tone="default" icon={<I name="layers" size={11}/>}>3 collections in context</RcaChip>
                  <div style={{ flex: 1 }}/>
                  <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>⌘↵</span>
                  <Btn size="sm" variant="primary" icon={<I name="arrow_r" size={13}/>}>Send</Btn>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function ChatMsgUser({ who, time, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Avatar name={who} size={22}/>
        <span style={{ fontSize: 13, fontWeight: 600 }}>You</span>
        <span style={{ fontSize: 11, color: RCA.textPaperD2 }}>{time}</span>
      </div>
      <div style={{ paddingLeft: 30, fontSize: 14, lineHeight: 1.55 }}>{children}</div>
    </div>
  );
}
function ChatMsgAgent({ children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 22, height: 22, borderRadius: 4, background: RCA.ink, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <RCAMark size={15} color={RCA.textDark}/>
        </div>
        <span style={{ fontSize: 13, fontWeight: 600 }}>KB Agent</span>
      </div>
      <div style={{ paddingLeft: 30, fontSize: 14, lineHeight: 1.55, color: RCA.textPaper }}>{children}</div>
    </div>
  );
}

// ============================================================
// Shared sidebar
// ============================================================
function KBSidebar({ active, onBack }) {
  return (
    <aside style={{ width: 240, borderRight: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", background: RCA.paper }}>
      <div style={{ padding: "20px 18px 16px", borderBottom: `1px solid ${RCA.paper3}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <RCAMark size={40}/>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1, gap: 6 }}>
            <div style={{ fontFamily: RCA.fSans, fontWeight: 800, fontSize: 24, letterSpacing: "-0.03em", display: "flex", alignItems: "center" }}>
              <span>RCA</span>
              <span style={{ width: 5, height: 5, background: RCA.accent, margin: "0 4px 0 6px", display: "inline-block" }}/>
              <span>3.0</span>
            </div>
            <div style={{ fontFamily: RCA.fMono, fontSize: 8.5, fontWeight: 500, color: RCA.textPaperD, letterSpacing: "0.08em", whiteSpace: "nowrap", textTransform: "uppercase" }}>
              Analysis <span style={{ color: RCA.accent }}>.</span> AI <span style={{ color: RCA.accent }}>.</span> Agent
            </div>
          </div>
        </div>
        <Btn variant="ghost" size="sm" icon={<I name="chev_l" size={13}/>} style={{ marginTop: 14 }} onClick={onBack}>Back to investigations</Btn>
      </div>
      <nav style={{ padding: 8, display: "flex", flexDirection: "column", gap: 1 }}>
        <KBNavItem icon="bug"      label="Investigations" onClick={onBack}/>
        <KBNavItem icon="layers"   label="Knowledge base" badge={KB_COLLECTIONS.length} active={active === "kb"}/>
        <KBNavItem icon="chat"     label="Chats"          badge={CHAT_HISTORY.length} active={active === "chats"}/>
        <KBNavItem icon="users"    label="People"/>
        <KBNavItem icon="settings" label="Settings"/>
      </nav>
    </aside>
  );
}

function KBNavItem({ icon, label, badge, active, onClick }) {
  return (
    <div onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "7px 10px", borderRadius: 4,
      background: active ? RCA.accentSoft : "transparent",
      color: active ? RCA.accentH : RCA.textPaper,
      cursor: "pointer",
    }}>
      <I name={icon} size={15} color={active ? RCA.accentH : RCA.textPaperD}/>
      <span style={{ fontSize: 13, fontWeight: active ? 600 : 400, flex: 1 }}>{label}</span>
      {badge != null && (
        <span className="mono" style={{ fontSize: 11, color: active ? RCA.accent : RCA.textPaperD2 }}>{badge}</span>
      )}
    </div>
  );
}

Object.assign(window, { KBDrawer, KBPage, ChatsPage, KB_COLLECTIONS, CHAT_HISTORY });
