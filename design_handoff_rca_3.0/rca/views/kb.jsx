// Knowledge Base — drawer (Ask agent), KB management page, Chat history page
// Uses RCA, Btn, RcaChip, Card, I, Avatar, CapsLabel from rca/system.jsx

// ============================================================
// Sample data
// ============================================================
const KB_SOURCES = [
  { id: "src-1",  type: "investigations", title: "Past investigations", count: 142, lastSync: "12 min ago", status: "ok",       owner: "auto",   desc: "All resolved & abandoned RCAs in the org. Auto-indexed.",  pinned: true },
  { id: "src-2",  type: "docs",           title: "Process SOPs",        count: 38,  lastSync: "1 h ago",   status: "ok",       owner: "Carol",  desc: "Standard operating procedures for SMT, reflow, AOI, paint." },
  { id: "src-3",  type: "docs",           title: "Equipment manuals",   count: 64,  lastSync: "yesterday",status: "ok",       owner: "Bob",    desc: "Reflow ovens, SMT placers, AOI machines — vendor PDFs." },
  { id: "src-4",  type: "wiki",           title: "Internal wiki",       count: 412, lastSync: "3 h ago",   status: "ok",       owner: "auto",   desc: "Confluence — engineering space + QA space.", pinned: true },
  { id: "src-5",  type: "datasets",       title: "Spec library",        count: 19,  lastSync: "2 days ago",status: "ok",       owner: "Carol",  desc: "Product specs · component datasheets · acceptance criteria." },
  { id: "src-6",  type: "datasets",       title: "Defect taxonomy",     count: 1,   lastSync: "1 wk ago",  status: "ok",       owner: "Carol",  desc: "Canonical defect codes and definitions." },
  { id: "src-7",  type: "external",       title: "IPC standards",       count: 27,  lastSync: "1 mo ago",  status: "stale",    owner: "Carol",  desc: "IPC-A-610, J-STD-001, others." },
  { id: "src-8",  type: "docs",           title: "Change-control matrix",count: 1,  lastSync: "—",         status: "indexing", owner: "Alice",  desc: "Live document — currently re-indexing after edit." },
  { id: "src-9",  type: "external",       title: "Supplier reports",    count: 84,  lastSync: "—",         status: "error",    owner: "Bob",    desc: "Last sync failed: missing credentials." },
];

const KB_SUGGESTIONS = [
  "What does IPC-A-610 say about void rate acceptance?",
  "Has reflow zone-3 drift been seen before?",
  "Show me 5-Why chains where the root was in change-control",
  "Summarize the last 3 wirebond investigations",
];

const CHAT_HISTORY = [
  { id: "c-12", title: "Void acceptance thresholds for BGA",     ws: "kb",     msgs: 14, updated: "12 min ago", pinned: true, snippet: "What does IPC-A-610 say about void rate…", tone: "accent" },
  { id: "c-11", title: "Past zone-3 drift incidents",            ws: "kb",     msgs: 8,  updated: "2 h ago",   pinned: true, snippet: "Has reflow zone-3 drift been seen before?" },
  { id: "c-10", title: "Wirebond pull-strength patterns",        ws: "kb",     msgs: 22, updated: "yesterday", snippet: "Trends across last 6 months on Sensor V2…" },
  { id: "c-9",  title: "Change-control coverage gaps",           ws: "kb",     msgs: 6,  updated: "yesterday", snippet: "Find investigations where root was matrix gap" },
  { id: "c-8",  title: "Reflow PID gains — best practices",      ws: "kb",     msgs: 18, updated: "2 days ago" },
  { id: "c-7",  title: "Defect taxonomy proposal",               ws: "kb",     msgs: 31, updated: "3 days ago", snippet: "Should we split 'void' into BGA-pad vs QFN-center?" },
  { id: "c-6",  title: "Solder voids on Line 3",                 ws: "INC-0142", msgs: 47, updated: "today",     snippet: "Inside investigation chat (linked)" },
  { id: "c-5",  title: "Top yield drops Q3",                     ws: "kb",     msgs: 9,  updated: "1 wk ago" },
  { id: "c-4",  title: "Onboarding: how to write an RCA",        ws: "kb",     msgs: 14, updated: "2 wk ago" },
  { id: "c-3",  title: "Paint ΔE acceptance",                    ws: "INC-0136", msgs: 12, updated: "2 wk ago", snippet: "Inside investigation chat (linked)" },
];

function sourceIcon(type) {
  return { investigations: "bug", docs: "file", wiki: "globe", datasets: "table", external: "download" }[type] || "file";
}

// Per-source documents (sample for a single source preview)
const KB_DOCS_INVESTIGATIONS = [
  { id: "INC-0119", title: "Reflow zone-3 drift on MX-7",         updated: "2025-12-04", chunks: 14, tags: ["reflow", "PID", "void"] },
  { id: "INC-0098", title: "MX-5 zone-3 drift, ambient creep",    updated: "2025-09-21", chunks: 11, tags: ["reflow", "baseline"] },
  { id: "INC-0072", title: "MX-7 voids, humidity contributor",    updated: "2025-06-12", chunks: 19, tags: ["reflow", "humidity"] },
  { id: "INC-0064", title: "Wirebond pull spec deviation",         updated: "2025-05-08", chunks: 8,  tags: ["wirebond"] },
  { id: "INC-0058", title: "Underfill voids on Module N4",         updated: "2025-04-22", chunks: 12, tags: ["underfill", "x-ray"] },
  { id: "INC-0042", title: "Paint ΔE shift after lot B-24-1209",   updated: "2024-12-18", chunks: 7,  tags: ["paint", "supplier"] },
];

const KB_CHUNKS_SAMPLE = [
  { id: "ch-12-008", doc: "INC-0119", section: "D4 · Root cause",
    text: "Reflow zone-3 PID gains, tuned for the prior throughput profile, failed to maintain 245°C set-point under the throughput increase logged on 2025-12-02. Drift of 3.0°C preceded the void spike by 28 minutes.",
    cited: 4 },
  { id: "ch-12-011", doc: "INC-0119", section: "D7 · Preventive",
    text: "Update change-control matrix to include throughput aggregation > 8% as a trigger for PID re-tune. Add SPC alarm for zone-actual vs set-point delta > 2°C over 15 min window.",
    cited: 12 },
  { id: "ch-09-004", doc: "INC-0098", section: "D4 · Root cause",
    text: "On MX-5, baseline void rate crept from 1.4% to 1.9% across August. Drift went undetected because alarms used absolute thresholds rather than baseline-anchored bands.",
    cited: 6 },
  { id: "ch-09-007", doc: "INC-0098", section: "D5 · Corrective",
    text: "Switched zone-3 SPC alarm to a Westgard-style baseline-anchored rule. Re-baselined every 14 days under steady throughput.",
    cited: 3 },
  { id: "ch-06-012", doc: "INC-0072", section: "D4 · Root cause",
    text: "Ambient humidity rose 12%RH on 2025-06-08. Paste open-life effectively shortened from 8h to ~5h. The void spike correlates with humidity × paste-age interaction, not zone-3 alone.",
    cited: 8 },
  { id: "ch-06-016", doc: "INC-0072", section: "D7 · Preventive",
    text: "Added HVAC zone-2 humidity sensor to SPC dashboard. Paste open-life adjusted to 6h when RH > 55%.",
    cited: 5 },
];

// ============================================================
// 1) KB CHAT DRAWER — slides in from right
// ============================================================
function KBDrawer({ open, onClose, onOpenChats, onOpenKB }) {
  const [messages, setMessages] = React.useState([
    { role: "agent", text: "Hi — ask me anything across your knowledge base. I'll cite the sources." },
  ]);
  const [pending, setPending] = React.useState(false);

  const send = (text) => {
    setMessages((m) => [...m, { role: "user", who: "AC", time: "now", text }]);
    setPending(true);
    setTimeout(() => {
      setMessages((m) => [...m, {
        role: "agent",
        text: <>
          <p style={{ margin: 0, marginBottom: 8 }}>
            IPC-A-610 sets <strong>Class 2 acceptance at &lt; 25% voiding</strong> for BGA solder joints; Class 3 (high-rel) at <strong>&lt; 9%</strong>. Your current MX-7 void rate of 3.2% is well within either spec, but the 2.3× <em>jump</em> from 1.4% baseline is the alarm — process drift not absolute threshold.
          </p>
        </>,
        citations: [
          { src: "IPC standards", n: 12, snippet: "Class 2 BGA voiding ≤ 25% by area" },
          { src: "Past investigations", n: 3, snippet: "INC-0119, INC-0098, INC-0072 — drift-driven, baseline = 1.4±0.3%" },
          { src: "Spec library", n: 1, snippet: "MX-7 process spec · void rate baseline ≤ 1.8%" },
        ],
      }]);
      setPending(false);
    }, 900);
  };

  if (!open) return null;
  return (
    <>
      {/* backdrop */}
      <div onClick={onClose} style={{
        position: "fixed", inset: 0,
        background: "rgba(20,22,28,0.35)",
        backdropFilter: "blur(2px)",
        zIndex: 60,
        animation: "kbFade 200ms ease",
      }}/>
      {/* drawer */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: 480, background: RCA.paper, borderLeft: `1px solid ${RCA.paper3}`,
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
              <span><span style={{ color: RCA.ok }}>●</span> 9 sources · 689 docs indexed</span>
              <span>·</span>
              <span onClick={onOpenKB} style={{ cursor: "pointer", textDecoration: "underline", textDecorationStyle: "dotted" }}>manage</span>
            </div>
          </div>
          <Btn size="sm" variant="ghost" icon={<I name="clock" size={13}/>} onClick={onOpenChats}>History</Btn>
          <Btn size="sm" variant="ghost" icon={<I name="x" size={14}/>} onClick={onClose}/>
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
                        <div style={{ fontSize: 12, color: RCA.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.src} <span style={{ color: RCA.textPaperD2, fontFamily: RCA.fMono, fontSize: 11 }}>· {c.n} {c.n === 1 ? "chunk" : "chunks"}</span></div>
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
              searching…
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
              <RcaChip tone="default" icon={<I name="filter" size={11}/>}>All sources</RcaChip>
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
// 2) KNOWLEDGE BASE MANAGEMENT PAGE
// ============================================================
function KBPage({ onBack, onAskAgent }) {
  const W = 1440, H = 900;
  const [selectedSrc, setSelectedSrc] = React.useState(null);

  return (
    <div className="rca" style={{ width: W, height: H, background: RCA.paper, display: "flex", overflow: "hidden", color: RCA.textPaper }}>
      <KBSidebar active="kb" onBack={onBack}/>
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* top bar */}
        <div style={{ height: 64, padding: "0 28px", display: "flex", alignItems: "center", borderBottom: `1px solid ${RCA.paper3}`, gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 38, width: 420, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={15} color={RCA.textPaperD}/>
            <span style={{ color: RCA.textPaperD, fontSize: 13, flex: 1 }}>Search documents, chunks, citations…</span>
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
              9 sources <span style={{ color: RCA.accent }}>·</span> 689 docs indexed
            </h1>
            <p style={{ color: RCA.textPaperD, fontSize: 14, marginTop: 8 }}>Everything the agent draws on when answering. Anyone can read; admins manage.</p>
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <KBMetric label="Last full sync" value="12 min ago" sub="auto · 2h cadence"/>
            <KBMetric label="Chunks indexed" value="48,412" sub="≈ 12.4 M tokens"/>
            <KBMetric label="Coverage" value="94%" sub="6% stale or error"/>
          </div>
        </div>

        {/* tabs */}
        <div style={{ padding: "0 28px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "stretch", gap: 28 }}>
          {[
            ["All sources", 9, true],
            ["Auto · investigations", 1],
            ["Docs & specs", 4],
            ["Wiki & web", 2],
            ["External", 2],
            ["Issues", 2, false, "warn"],
          ].map(([t, c, act, tone], i) => (
            <div key={i} style={{ padding: "12px 0", borderBottom: act ? `2px solid ${RCA.accent}` : `2px solid transparent`, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <span style={{ fontSize: 14, fontWeight: act ? 600 : 400, color: act ? RCA.ink : RCA.textPaperD }}>{t}</span>
              <span className="mono" style={{ fontSize: 11, color: tone === "warn" ? RCA.warn : (act ? RCA.accent : RCA.textPaperD2) }}>{c}</span>
            </div>
          ))}
        </div>

        {/* action strip */}
        <div style={{ padding: "16px 28px", display: "flex", gap: 8, alignItems: "center" }}>
          <Btn size="sm" icon={<I name="filter" size={13}/>}>Filter</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12}/>}>Status · any</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12}/>}>Owner · any</Btn>
          <div style={{ flex: 1 }}/>
          <Btn size="sm" variant="ghost" icon={<I name="play" size={13}/>}>Sync all</Btn>
          <Btn size="sm" icon={<I name="upload" size={13}/>}>Upload</Btn>
          <Btn size="sm" variant="primary" icon={<I name="plus" size={13}/>}>Connect source</Btn>
        </div>

        {/* source list */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "0 28px 28px" }}>
          <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "32px 2.4fr 1fr 1fr 0.9fr 1fr 32px", padding: "10px 16px", borderBottom: `1px solid ${RCA.paper3}`, alignItems: "center", gap: 10 }}>
              <div></div>
              {["Source","Type","Docs","Status","Last sync"].map((h, i) => (
                <div key={i} className="caps" style={{ fontSize: 10, color: RCA.textPaperD }}>{h}</div>
              ))}
              <div></div>
            </div>
            {KB_SOURCES.map((s, i) => (
              <div key={s.id} onClick={() => setSelectedSrc(s)} style={{ display: "grid", gridTemplateColumns: "32px 2.4fr 1fr 1fr 0.9fr 1fr 32px", padding: "14px 16px", alignItems: "center", gap: 10, borderBottom: i < KB_SOURCES.length - 1 ? `1px solid ${RCA.paper3}` : "none", cursor: "pointer", background: i === 0 ? RCA.accentSoft + "40" : "transparent" }}>
                <div style={{ color: s.pinned ? RCA.accent : RCA.textPaperD2 }}>
                  <I name="pin" size={14}/>
                </div>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10, minWidth: 0 }}>
                  <div style={{ width: 32, height: 32, borderRadius: 6, background: RCA.paper2, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <I name={sourceIcon(s.type)} size={15} color={RCA.ink2}/>
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: RCA.ink, marginBottom: 2 }}>{s.title}</div>
                    <div style={{ fontSize: 12, color: RCA.textPaperD, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.desc}</div>
                  </div>
                </div>
                <div><RcaChip tone="outline">{s.type}</RcaChip></div>
                <div className="mono" style={{ fontSize: 13 }}>{s.count.toLocaleString()}</div>
                <div>
                  {s.status === "ok" && <RcaChip dot tone="ok">indexed</RcaChip>}
                  {s.status === "indexing" && <RcaChip dot tone="accent">indexing…</RcaChip>}
                  {s.status === "stale" && <RcaChip dot tone="warn">stale</RcaChip>}
                  {s.status === "error" && <RcaChip dot tone="err">error</RcaChip>}
                </div>
                <div style={{ fontSize: 13, color: RCA.textPaperD, display: "flex", alignItems: "center", gap: 8 }}>
                  <span>{s.lastSync}</span>
                  {s.owner !== "auto" && <Avatar name={s.owner.slice(0,2)} size={20}/>}
                </div>
                <div title="Source actions" style={{ color: RCA.textPaperD2, display: "flex", justifyContent: "center" }}>
                  <I name="dots_v" size={16}/>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, fontSize: 12, color: RCA.textPaperD, display: "flex", alignItems: "center", gap: 6 }}>
            <I name="sparkle" size={12} color={RCA.accent}/>
            New investigations are auto-added to the KB when status flips to <strong style={{ color: RCA.ink }}>resolved</strong> or <strong style={{ color: RCA.ink }}>abandoned</strong>.
          </div>
        </div>
      </main>

      {selectedSrc && <SourceDetailPanel source={selectedSrc} onClose={() => setSelectedSrc(null)}/>}
    </div>
  );
}

// SOURCE DETAIL drawer (right side) — documents + chunks
function SourceDetailPanel({ source, onClose }) {
  const showInvestigationDocs = source.type === "investigations";
  return (
    <>
      <div onClick={onClose} style={{
        position: "absolute", inset: 0,
        background: "rgba(20,22,28,0.25)",
        zIndex: 40,
      }}/>
      <div className="rca" style={{
        position: "absolute", top: 0, right: 0, bottom: 0,
        width: 680, background: RCA.paper,
        borderLeft: `1px solid ${RCA.paper3}`,
        boxShadow: "-20px 0 40px rgba(20,22,28,.12)",
        zIndex: 41, display: "flex", flexDirection: "column",
        animation: "kbSlide 220ms cubic-bezier(.2,.7,.2,1)",
      }}>
        <style>{`@keyframes kbSlide { from { transform: translateX(100%); } to { transform: translateX(0); } }`}</style>

        {/* header */}
        <div style={{ padding: "20px 24px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "flex-start", gap: 14 }}>
          <div style={{ width: 44, height: 44, borderRadius: 8, background: RCA.paper2, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
            <I name={sourceIcon(source.type)} size={20} color={RCA.ink2}/>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <RcaChip tone="outline">{source.type}</RcaChip>
              {source.status === "ok" && <RcaChip dot tone="ok">indexed</RcaChip>}
              {source.status === "indexing" && <RcaChip dot tone="accent">indexing…</RcaChip>}
              {source.status === "stale" && <RcaChip dot tone="warn">stale</RcaChip>}
              {source.status === "error" && <RcaChip dot tone="err">error</RcaChip>}
            </div>
            <h2 className="display" style={{ fontSize: 22, marginBottom: 4 }}>{source.title}</h2>
            <p style={{ fontSize: 13, color: RCA.textPaperD, margin: 0, lineHeight: 1.5 }}>{source.desc}</p>
          </div>
          <Btn size="sm" variant="ghost" icon={<I name="x" size={14}/>} onClick={onClose}/>
        </div>

        {/* meta strip */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", borderBottom: `1px solid ${RCA.paper3}`, background: RCA.paper2 }}>
          {[
            ["Documents", source.count.toLocaleString()],
            ["Chunks", Math.round(source.count * 8).toLocaleString()],
            ["Last sync", source.lastSync],
            ["Owner", source.owner === "auto" ? "— auto" : source.owner],
          ].map(([k, v], i) => (
            <div key={i} style={{ padding: "12px 14px", borderRight: i < 3 ? `1px solid ${RCA.paper3}` : "none" }}>
              <CapsLabel style={{ marginBottom: 4, fontSize: 9 }}>{k}</CapsLabel>
              <div style={{ fontSize: 14, fontWeight: 600 }}>{v}</div>
            </div>
          ))}
        </div>

        {/* action strip */}
        <div style={{ padding: "12px 24px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", gap: 8 }}>
          <Btn size="sm" icon={<I name="play" size={13}/>}>Sync now</Btn>
          <Btn size="sm" variant="ghost" icon={<I name="download" size={13}/>}>Export</Btn>
          {source.status === "error" && (
            <Btn size="sm" variant="primary" icon={<I name="settings" size={13}/>}>Fix credentials</Btn>
          )}
          <div style={{ flex: 1 }}/>
          <Btn size="sm" variant="ghost" icon={<I name="settings" size={13}/>}>Configure</Btn>
        </div>

        {/* tab strip */}
        <div style={{ padding: "0 24px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", gap: 24 }}>
          {[["Documents", source.count, true], ["Chunks", Math.round(source.count * 8)], ["Index activity"], ["Permissions"]].map(([t, c, act], i) => (
            <div key={i} style={{ padding: "10px 0", borderBottom: act ? `2px solid ${RCA.accent}` : `2px solid transparent`, display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
              <span style={{ fontSize: 13, fontWeight: act ? 600 : 400, color: act ? RCA.ink : RCA.textPaperD }}>{t}</span>
              {c != null && <span className="mono" style={{ fontSize: 11, color: act ? RCA.accent : RCA.textPaperD2 }}>{c.toLocaleString()}</span>}
            </div>
          ))}
        </div>

        {/* body */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "14px 24px 24px" }}>
          {showInvestigationDocs ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 10px", height: 34, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6, flex: 1 }}>
                  <I name="search" size={13} color={RCA.textPaperD}/>
                  <span style={{ color: RCA.textPaperD, fontSize: 12 }}>Filter documents…</span>
                </div>
                <Btn size="sm" iconRight={<I name="chev_d" size={11}/>}>Newest</Btn>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {KB_DOCS_INVESTIGATIONS.map((d) => (
                  <div key={d.id} style={{ display: "grid", gridTemplateColumns: "90px 1fr 110px 60px 18px", gap: 14, alignItems: "center", padding: "10px 14px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6, cursor: "pointer" }}>
                    <span className="mono" style={{ fontSize: 12, color: RCA.accent, fontWeight: 600 }}>{d.id}</span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: RCA.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.title}</div>
                      <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                        {d.tags.map((t) => <RcaChip key={t} tone="outline">{t}</RcaChip>)}
                      </div>
                    </div>
                    <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD }}>{d.updated}</span>
                    <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2, textAlign: "right" }}>{d.chunks} chunks</span>
                    <I name="chev_r" size={13} color={RCA.textPaperD2}/>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: `1px solid ${RCA.paper3}` }}>
                <CapsLabel style={{ marginBottom: 10 }}>Top chunks · most cited</CapsLabel>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {KB_CHUNKS_SAMPLE.slice(0, 4).map((c) => (
                    <div key={c.id} style={{ padding: "10px 14px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                        <span className="mono" style={{ fontSize: 10, color: RCA.accent, fontWeight: 600 }}>{c.doc}</span>
                        <span style={{ fontSize: 11, color: RCA.textPaperD }}>· {c.section}</span>
                        <div style={{ flex: 1 }}/>
                        <RcaChip tone="accent" icon={<I name="chat" size={10}/>}>cited {c.cited}×</RcaChip>
                      </div>
                      <div style={{ fontSize: 13, color: RCA.textPaper, lineHeight: 1.55 }}>{c.text}</div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <>
              <div style={{ padding: "14px 16px", background: RCA.paper2, border: `1px dashed ${RCA.paper3}`, borderRadius: 6, display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                <I name="file" size={16} color={RCA.textPaperD}/>
                <span style={{ fontSize: 13, color: RCA.textPaperD }}>Document list redacted in prototype — {source.count} files indexed.</span>
              </div>
              <CapsLabel style={{ marginBottom: 10 }}>Sample chunks</CapsLabel>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {[
                  { sec: source.type === "external" ? "IPC-A-610G § 8.3.5.7" : source.type === "docs" ? "SOP-RFLW-12 § 4.2" : "Acceptance criteria", text: source.type === "external" ? "Class 2 BGA solder joints shall not exceed 25% voiding by area when assessed via X-ray transmission imaging." : "Re-tune PID gains for reflow zone-3 whenever throughput aggregation exceeds 8% of nominal across any 24-hour window." },
                  { sec: "Glossary", text: "void rate — area-fraction of voiding in solder joints measured by AOI/X-ray, expressed as percent." },
                  { sec: "Acceptance criteria", text: source.type === "datasets" ? "MX-7 board: void rate baseline ≤ 1.8%. Investigate when 7-day rolling mean exceeds 2.4%." : "AOI sampling rate shall be raised to 1/10 whenever a containment lot is active on the affected line." },
                ].map((c, i) => (
                  <div key={i} style={{ padding: "10px 14px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <span className="mono" style={{ fontSize: 11, color: RCA.accent }}>{c.sec}</span>
                      <div style={{ flex: 1 }}/>
                      <RcaChip tone="accent" icon={<I name="chat" size={10}/>}>cited {6 - i * 2}×</RcaChip>
                    </div>
                    <div style={{ fontSize: 13, color: RCA.textPaper, lineHeight: 1.55 }}>{c.text}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </>
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
// 3) CHAT HISTORY PAGE
// ============================================================
function ChatsPage({ onBack, onOpenChat, onAskAgent }) {
  const W = 1440, H = 900;
  const [selected, setSelected] = React.useState(CHAT_HISTORY[0].id);
  const chat = CHAT_HISTORY.find((c) => c.id === selected) || CHAT_HISTORY[0];

  return (
    <div className="rca" style={{ width: W, height: H, background: RCA.paper, display: "flex", overflow: "hidden", color: RCA.textPaper }}>
      <KBSidebar active="chats" onBack={onBack}/>
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* top bar */}
        <div style={{ height: 64, padding: "0 28px", display: "flex", alignItems: "center", borderBottom: `1px solid ${RCA.paper3}`, gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 38, width: 420, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={15} color={RCA.textPaperD}/>
            <span style={{ color: RCA.textPaperD, fontSize: 13, flex: 1 }}>Search chats by title, content, citation…</span>
          </div>
          <div style={{ flex: 1 }}/>
          <Btn variant="ghost" icon={<I name="bell" size={15}/>}>3</Btn>
          <Btn icon={<I name="sparkle" size={14}/>} onClick={onAskAgent}>New chat</Btn>
        </div>

        {/* page body — left list, right preview */}
        <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
          {/* list */}
          <div style={{ width: 460, borderRight: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "20px 22px 8px" }}>
              <CapsLabel style={{ marginBottom: 8 }}>Conversations</CapsLabel>
              <h1 className="display" style={{ fontSize: 26 }}>
                {CHAT_HISTORY.length} chats <span style={{ color: RCA.accent }}>·</span> 8 KB · 2 linked
              </h1>
            </div>
            <div style={{ padding: "8px 18px", display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[["All", true], ["Pinned"], ["KB"], ["Linked to investigations"], ["Shared with me"]].map(([t, act], i) => (
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
                    <RcaChip tone={c.ws === "kb" ? "default" : "accent"} icon={<I name={c.ws === "kb" ? "globe" : "bug"} size={10}/>}>
                      {c.ws === "kb" ? "KB" : c.ws}
                    </RcaChip>
                    <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>{c.msgs} msgs</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* preview */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "20px 28px 16px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <RcaChip tone={chat.ws === "kb" ? "default" : "accent"} icon={<I name={chat.ws === "kb" ? "globe" : "bug"} size={10}/>}>
                    {chat.ws === "kb" ? "KB chat" : "Investigation chat · " + chat.ws}
                  </RcaChip>
                  {chat.pinned && <RcaChip tone="accent" icon={<I name="pin" size={10}/>}>pinned</RcaChip>}
                </div>
                <h2 className="display" style={{ fontSize: 22, marginBottom: 4 }}>{chat.title}</h2>
                <div style={{ fontSize: 12, color: RCA.textPaperD, fontFamily: RCA.fMono }}>{chat.msgs} messages · updated {chat.updated} · started by Alice</div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <Btn size="sm" variant="ghost" icon={<I name="pin" size={13}/>}/>
                <Btn size="sm" variant="ghost" icon={<I name="download" size={13}/>}>Export</Btn>
                <Btn size="sm" variant="ghost" icon={<I name="users" size={13}/>}>Share</Btn>
                <Btn size="sm" variant="primary" iconRight={<I name="arrow_r" size={13}/>} onClick={onOpenChat}>Continue chat</Btn>
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
                    { src: "Past investigations", n: 3, snippet: "INC-0119 · INC-0098 · INC-0072" },
                    { src: "Process SOPs", n: 1, snippet: "SOP-RFLW-12 · zone-3 PID tuning" },
                  ].map((c, j) => (
                    <div key={j} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                      <span className="mono" style={{ fontSize: 10, color: RCA.accent, fontWeight: 700, minWidth: 22 }}>[{j+1}]</span>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 12 }}>{c.src} <span style={{ color: RCA.textPaperD2, fontFamily: RCA.fMono, fontSize: 11 }}>· {c.n}</span></div>
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
// Shared sidebar used by KB & Chats pages
// ============================================================
function KBSidebar({ active, onBack }) {
  return (
    <aside style={{ width: 240, borderRight: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", padding: "0", background: RCA.paper }}>
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
        <KBNavItem icon="bug"     label="Investigations" onClick={onBack}/>
        <KBNavItem icon="layers"  label="Knowledge base" badge={9}  active={active === "kb"}/>
        <KBNavItem icon="chat"    label="Chats"          badge={10} active={active === "chats"}/>
        <KBNavItem icon="users"   label="People"/>
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

Object.assign(window, { KBDrawer, KBPage, ChatsPage, SourceDetailPanel, KB_SOURCES, CHAT_HISTORY });
