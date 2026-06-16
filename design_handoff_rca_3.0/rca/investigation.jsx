// RCA Investigation workspace — hi-fi VSCode-style editor reframed for RCA.
// Now stateful: file tabs drive the main view; agent can also navigate.

const INV_W = 1440;
const INV_H = 900;

const INV_TABS = [
  { view: "brief",    file: "brief.md",         icon: "file",     modified: true },
  { view: "spc",      file: "drift.ipynb",      icon: "chart",    modified: true },
  { view: "pareto",   file: "pareto.ipynb",     icon: "pareto" },
  { view: "fishbone", file: "fishbone.canvas",  icon: "fishbone" },
  { view: "fivewhy",  file: "5-why.md",         icon: "file" },
  { view: "report",   file: "report.md",        icon: "file" },
];

const INV_BREADCRUMBS = {
  brief:    [["folder","analyses"],["file","brief.md"],["file","Hypothesis 1 — reflow drift"]],
  spc:      [["folder","analyses"],["chart","drift.ipynb"],["file","zone-3 temperature vs voids"]],
  pareto:   [["folder","analyses"],["pareto","pareto.ipynb"],["file","failure modes · 14d"]],
  fishbone: [["folder","analyses"],["fishbone","fishbone.canvas"],["file","6M cause categories"]],
  fivewhy:  [["folder","analyses"],["file","5-why.md"],["file","root cause chain"]],
  report:   [["folder","analyses"],["file","report.md"],["file","8D draft"]],
};

function InvestigationRCA({ onBack } = {}) {
  const [view, setView] = React.useState("brief");
  return (
    <div className="rca" style={{ width: INV_W, height: INV_H, background: RCA.paper, display: "flex", flexDirection: "column", overflow: "hidden", color: RCA.textPaper }}>

      {/* TOP BAR */}
      <div style={{ height: 52, padding: "0 18px", display: "flex", alignItems: "center", gap: 14, borderBottom: `1px solid ${RCA.paper3}`, background: RCA.paper }}>
        {onBack && (
          <Btn size="sm" variant="ghost" icon={<I name="chev_l" size={14}/>} onClick={onBack}>All</Btn>
        )}
        <RCALockup size={22} compact/>
        <div style={{ width: 1, height: 22, background: RCA.paper3, margin: "0 4px" }}/>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13, color: RCA.textPaperD }}>acme</span>
          <I name="chev_r" size={11} color={RCA.textPaperD2}/>
          <span style={{ fontSize: 13, color: RCA.textPaperD }}>SMT process</span>
          <I name="chev_r" size={11} color={RCA.textPaperD2}/>
          <span style={{ fontSize: 13, color: RCA.ink, fontWeight: 600 }}>Solder voids spike</span>
          <RcaChip dot tone="err" style={{ marginLeft: 6 }}>P1</RcaChip>
          <RcaChip dot tone="warn">triaging</RcaChip>
        </div>
        <div style={{ flex: 1 }}/>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 32, width: 320, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
          <I name="search" size={14} color={RCA.textPaperD}/>
          <span style={{ color: RCA.textPaperD, fontSize: 13, flex: 1 }}>Files, defects, runs, lots…</span>
          <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD, padding: "1px 5px", border: `1px solid ${RCA.paper3}`, borderRadius: 3 }}>⌘P</span>
        </div>
        <Btn size="sm" variant="ghost" iconRight={<I name="chev_d" size={11}/>}>claude-opus-4</Btn>
        <Btn size="sm" variant="ghost" icon={<I name="users" size={14}/>}>4</Btn>
        <Btn size="sm" variant="ghost" icon={<I name="bell" size={14}/>}/>
        <Avatar name="AC" size={28}/>
      </div>

      {/* BODY */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

        {/* ACTIVITY BAR */}
        <div style={{ width: 50, borderRight: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", alignItems: "center", padding: "10px 0", gap: 4 }}>
          {[
            ["folder", "Evidence", true],
            ["search", "Search"],
            ["git", "Source · 3", null, "3"],
            ["sparkle", "Agent"],
            ["bug", "Defect map"],
            ["clock", "History"],
            ["users", "Reviewers"],
          ].map(([icn, label, active, badge], i) => (
            <div key={i} style={{ width: 40, height: 40, display: "flex", alignItems: "center", justifyContent: "center", borderLeft: active ? `2px solid ${RCA.accent}` : `2px solid transparent`, background: active ? RCA.accentSoft : "transparent", borderRadius: 4, position: "relative" }}>
              <I name={icn} size={18} color={active ? RCA.accentH : RCA.textPaperD}/>
              {badge && <span style={{ position: "absolute", top: 4, right: 4, minWidth: 14, height: 14, padding: "0 3px", borderRadius: 7, background: RCA.accent, color: RCA.white, fontSize: 9, fontFamily: RCA.fMono, display: "flex", alignItems: "center", justifyContent: "center" }}>{badge}</span>}
            </div>
          ))}
          <div style={{ flex: 1 }}/>
          <I name="settings" size={18} color={RCA.textPaperD}/>
        </div>

        {/* SIDEBAR */}
        <aside style={{ width: 260, borderRight: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", overflow: "hidden", background: RCA.paper }}>
          <div style={{ padding: "12px 16px 8px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <CapsLabel>Evidence</CapsLabel>
            <I name="plus" size={14} color={RCA.textPaperD}/>
          </div>

          <FileSection label="Open">
            {INV_TABS.map((t, i) => (
              <div key={t.view} onClick={() => setView(t.view)} style={{ cursor: "pointer" }}>
                <FileRow icon={t.icon} label={t.file} active={view === t.view} modified={t.modified}/>
              </div>
            ))}
          </FileSection>

          <EvidenceTree view={view} setView={setView}/>

          <FileSection label="Outline">
            <OutlineRow label="Context"/>
            <OutlineRow label="Hypothesis 1 — reflow drift" active={view === "brief" || view === "spc"}/>
            <OutlineRow label="Hypothesis 2 — paste age"/>
            <OutlineRow label="Hypothesis 3 — squeegee pressure"/>
            <OutlineRow label="Corrective actions" active={view === "report" || view === "fivewhy"}/>
          </FileSection>

          <div style={{ marginTop: "auto", padding: 14, borderTop: `1px solid ${RCA.paper3}`, background: RCA.paper2 }}>
            <CapsLabel style={{ marginBottom: 6 }}>Investigation</CapsLabel>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", rowGap: 4, columnGap: 8, fontSize: 12 }}>
              <span style={{ color: RCA.textPaperD }}>Severity</span><RcaChip dot tone="err" style={{ width: "fit-content" }}>P1 · critical</RcaChip>
              <span style={{ color: RCA.textPaperD }}>Status</span><span>triaging</span>
              <span style={{ color: RCA.textPaperD }}>Owner</span><span>Alice Chen</span>
              <span style={{ color: RCA.textPaperD }}>Topic</span>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                <RcaChip tone="outline" style={{ width: "fit-content" }}>Reflow zone-3</RcaChip>
              </div>
              <span style={{ color: RCA.textPaperD }}>Opened</span><span>08-14 14:32</span>
            </div>
          </div>
        </aside>

        {/* EDITOR + BOTTOM PANEL */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
          {/* tabs */}
          <div style={{ height: 38, borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "stretch", background: RCA.paper, overflow: "hidden" }}>
            {INV_TABS.map((t, i) => (
              <div key={t.view} onClick={() => setView(t.view)} style={{ cursor: "pointer", display: "flex" }}>
                <Tab icon={t.icon} label={t.file} active={view === t.view} modified={t.modified}/>
              </div>
            ))}
            <div style={{ flex: 1 }}/>
            <div style={{ padding: "0 12px", display: "flex", alignItems: "center", gap: 8 }}>
              <Btn size="sm" variant="ghost" icon={<I name="split" size={13}/>}/>
              <Btn size="sm" variant="ghost" icon={<I name="layers" size={13}/>}/>
              <Btn size="sm" icon={<I name="play" size={13}/>}>Run all</Btn>
            </div>
          </div>

          {/* breadcrumb */}
          <div style={{ height: 28, borderBottom: `1px solid ${RCA.paper3}`, padding: "0 18px", display: "flex", alignItems: "center", gap: 6 }}>
            {INV_BREADCRUMBS[view].map(([icn, label], i, arr) => (
              <React.Fragment key={i}>
                <I name={icn} size={11} color={RCA.textPaperD}/>
                <span style={{ fontSize: 11, color: i === arr.length - 1 ? RCA.ink : RCA.textPaperD }}>{label}</span>
                {i < arr.length - 1 && <I name="chev_r" size={10} color={RCA.textPaperD2}/>}
              </React.Fragment>
            ))}
            <div style={{ flex: 1 }}/>
            <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>autosaved 12s ago</span>
          </div>

          {/* Co-drafting banner — always visible above content (except in report view) */}
          {view !== "report" && (
            <ReportBanner onOpen={() => setView("report")}/>
          )}

          {/* MAIN CONTENT — switches on view */}
          <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "20px 22px" }}>
            {view === "brief"    && <BriefView/>}
            {view === "spc"      && <SPCAnalysisView/>}
            {view === "pareto"   && <ParetoView/>}
            {view === "fishbone" && <FishboneView/>}
            {view === "fivewhy"  && <FiveWhyView/>}
            {view === "report"   && <ReportView/>}
          </div>

          {/* BOTTOM PANEL */}
          <div style={{ height: 200, borderTop: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", overflow: "hidden", background: RCA.paper }}>
            <div style={{ height: 32, borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "stretch", paddingLeft: 14 }}>
              {[
                ["Problems", 2, false],
                ["Output", null, false],
                ["Terminal", 1, false],
                ["Agent log", null, true],
                ["Run history", null, false],
              ].map(([t, b, act], i) => (
                <div key={i} style={{ padding: "0 14px", display: "flex", alignItems: "center", gap: 6, borderBottom: act ? `2px solid ${RCA.accent}` : "2px solid transparent" }}>
                  <span className="caps" style={{ fontSize: 11, color: act ? RCA.ink : RCA.textPaperD }}>{t}</span>
                  {b != null && <span style={{ minWidth: 16, padding: "0 5px", borderRadius: 7, background: act ? RCA.accent : RCA.paper3, color: act ? RCA.white : RCA.textPaperD, fontSize: 10, fontFamily: RCA.fMono }}>{b}</span>}
                </div>
              ))}
              <div style={{ flex: 1 }}/>
              <div style={{ padding: "0 12px", display: "flex", alignItems: "center", gap: 8, color: RCA.textPaperD }}>
                <I name="split" size={13}/><I name="minus" size={13}/><I name="x" size={13}/>
              </div>
            </div>
            <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "10px 16px", display: "flex", flexDirection: "column", gap: 6, fontFamily: RCA.fMono, fontSize: 12 }}>
              <AgentLogLine t="14:32:08" kind="plan">drafted 6-step plan · Hypothesis 1 (reflow drift)</AgentLogLine>
              <AgentLogLine t="14:32:11" kind="tool">spc.read("reflow.zone3", window="14d") → 20,160 samples</AgentLogLine>
              <AgentLogLine t="14:32:13" kind="tool">defects.aoi("MX-7", lot="25-W14") → 412 records, void_rate=3.2%</AgentLogLine>
              <AgentLogLine t="14:32:15" kind="insight">drift of 3.2°C in zone-3 actual precedes void spike by 30 min</AgentLogLine>
              <AgentLogLine t="14:32:18" kind="tool">correlate.find(target="void_rate", candidates=4)…</AgentLogLine>
              <AgentLogLine t="14:32:22" kind="running">step 4/6 · running correlation</AgentLogLine>
            </div>
          </div>
        </div>

        {/* AGENT PANEL */}
        <AgentPanel onView={setView} currentView={view}/>
      </div>

      {/* STATUS BAR */}
      <div style={{ height: 28, background: RCA.ink, color: RCA.textDark, display: "flex", alignItems: "center", padding: "0 14px", gap: 18, fontSize: 11, fontFamily: RCA.fMono }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}><I name="git" size={11}/>feat/zone-3-drift</span>
        <span>↑ 2 ↓ 0</span>
        <span style={{ display: "flex", alignItems: "center", gap: 4, color: RCA.err }}><I name="x" size={11}/>2</span>
        <span style={{ display: "flex", alignItems: "center", gap: 4, color: RCA.warn }}><I name="bell" size={11}/>1</span>
        <span style={{ color: RCA.textDarkD }}>·</span>
        <span><span style={{ color: RCA.accent }}>●</span> agent · correlating</span>
        <span style={{ color: RCA.textDarkD }}>4 watching</span>
        <div style={{ flex: 1 }}/>
        <span>Ln 14, Col 23</span>
        <span>UTF-8</span>
        <span>{view === "brief" ? "Markdown" : view === "report" ? "Markdown" : view === "fishbone" ? "Canvas" : "Python · ipynb"}</span>
        <span><span style={{ color: RCA.ok }}>●</span> kernel py3.11 idle</span>
        <span>Alice</span>
      </div>
    </div>
  );
}

// ============================================================
// View content components
// ============================================================
function BriefView() {
  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <CapsLabel style={{ color: RCA.accent, marginBottom: 8 }}>INVESTIGATION</CapsLabel>
        <h1 className="display" style={{ fontSize: 32, lineHeight: 1.1, marginBottom: 6 }}>
          Solder voids spike on Line 3 <span style={{ color: RCA.accent }}>·</span> 2.3× baseline
        </h1>
        <p style={{ fontSize: 14, color: RCA.textPaperD, margin: 0, lineHeight: 1.5, maxWidth: 720 }}>
          AOI void rate climbed from 1.4% to 3.2% starting 08-14 14:00, sustained across 4 shifts. Affects MX-7 board, lot 25-W14. No process-side changes logged. Reflow zone-3 sensor shows simultaneous temperature drift.
        </p>
      </div>
      <NotebookCellRCA n={1} kind="md">
        <h3 className="display" style={{ fontSize: 18, marginBottom: 8 }}>Hypothesis 1 — reflow zone-3 drift</h3>
        <p style={{ fontSize: 14, color: RCA.textPaper, margin: 0, lineHeight: 1.55 }}>
          Zone-3 set-point is 245°C. The drift began 30 min before the void spike. If the correlation holds across the 4-shift window, the drift is the likely upstream cause. Verify against process spec.
        </p>
      </NotebookCellRCA>
      <NotebookCellRCA n={2} kind="md">
        <h3 className="display" style={{ fontSize: 18, marginBottom: 8 }}>Hypothesis 2 — paste age past open-life</h3>
        <p style={{ fontSize: 14, color: RCA.textPaper, margin: 0, lineHeight: 1.55 }}>
          Open-life spec is 8h. Paste lot was opened at 06:00 — voids onset 14:00 is 8h exactly. Worth a check; lower prior given drift evidence.
        </p>
      </NotebookCellRCA>
      <NotebookCellRCA n={3} kind="md">
        <h3 className="display" style={{ fontSize: 18, marginBottom: 8 }}>Hypothesis 3 — squeegee pressure</h3>
        <p style={{ fontSize: 14, color: RCA.textPaper, margin: 0, lineHeight: 1.55 }}>
          Maintenance log shows no pressure adjustment in last 14d. Low prior unless paste-print SPC also drifted.
        </p>
      </NotebookCellRCA>
    </>
  );
}

function ReportBanner({ onOpen }) {
  return (
    <div style={{
      margin: "10px 22px 0",
      padding: "10px 14px",
      background: RCA.ink,
      borderRadius: 6,
      display: "flex", alignItems: "center", gap: 14,
      color: RCA.textDark,
    }}>
      <I name="file" size={16} color={RCA.accent}/>
      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Final report</span>
        <span style={{ fontFamily: RCA.fMono, fontSize: 12, color: RCA.accent }}>v3 · current</span>
        <span style={{ fontFamily: RCA.fMono, fontSize: 11, color: RCA.textDarkD }}>
          v2 superseded 16:08 · v1 superseded 14:48
        </span>
      </div>
      <Btn size="sm" variant="ghost" onDark icon={<I name="sparkle" size={12}/>}>Generate new version</Btn>
      <Btn size="sm" variant="solid" onDark iconRight={<I name="arrow_r" size={12}/>} onClick={onOpen}>Open</Btn>
    </div>
  );
}

function SPCAnalysisView() {
  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <CapsLabel style={{ color: RCA.accent, marginBottom: 8 }}>01 · zone-3-drift.ipynb</CapsLabel>
        <h1 className="display" style={{ fontSize: 26, lineHeight: 1.15 }}>SPC · zone-3 temperature vs void rate</h1>
      </div>
      <NotebookCellRCA n={1} kind="py" status="ok" duration="0.34s" active>
        <pre className="mono" style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: RCA.ink }}>
{`from rca import spc, defects

z3 = spc.read("reflow.zone3", window="14d")
voids = defects.aoi("MX-7", lot="25-W14")

z3.with(voids).plot(annotate="2026-08-14T14:00")`}
        </pre>
      </NotebookCellRCA>
      <OutputCell label="line · zone-3 set-point vs actual, void rate overlay">
        <ChartSPC/>
      </OutputCell>
      <Callout icon="sparkle" tone="accent" title="Agent observation" body="Zone-3 actual temperature drifted from 245.0°C to 241.8°C between 08-14 13:30 and 14:00. The drop precedes the void-rate spike by ~30 minutes. Drift is sustained; no PID retune visible in the controller log."/>
      <NotebookCellRCA n={2} kind="py" status="running" active>
        <pre className="mono" style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: RCA.ink }}>
{`from rca import correlate

candidates = ["reflow.zone3", "paste.press", "paste.age", "humidity"]

correlate.find(target="void_rate",
               window="7d",
               candidates=candidates,
               min_r=0.4)`}
        </pre>
      </NotebookCellRCA>
    </>
  );
}


// ============================================================
// Sub-components
// ============================================================
function FileSection({ label, children }) {
  return (
    <div style={{ padding: "4px 0" }}>
      <div style={{ padding: "4px 16px", display: "flex", alignItems: "center", gap: 4 }}>
        <I name="chev_d" size={10} color={RCA.textPaperD2}/>
        <CapsLabel style={{ fontSize: 10 }}>{label}</CapsLabel>
      </div>
      <div>{children}</div>
    </div>
  );
}

function FileRow({ icon, label, depth = 0, active, modified, expanded, scm }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6,
      padding: "4px 16px 4px " + (16 + depth * 14) + "px",
      background: active ? RCA.accentSoft + "55" : "transparent",
      borderLeft: active ? `2px solid ${RCA.accent}` : `2px solid transparent`,
      marginLeft: -2,
      cursor: "pointer", position: "relative",
    }}>
      <I name={icon} size={13} color={active ? RCA.accentH : RCA.textPaperD}/>
      <span style={{ fontSize: 13, color: active ? RCA.ink : RCA.textPaper, flex: 1, fontWeight: active ? 500 : 400 }}>{label}</span>
      {modified && <span style={{ width: 6, height: 6, borderRadius: "50%", background: RCA.warn }}/>}
      {scm && <span className="mono" style={{ fontSize: 10, fontWeight: 600, color: scm === "M" ? RCA.warn : scm === "A" ? RCA.ok : RCA.accent }}>{scm}</span>}
    </div>
  );
}

// ---- evidence tree node (recursive) ----
function EvNode({ node, depth, open, toggle, view, onSelect, renaming, setRenaming, onRename, onCtx }) {
  const pad = 16 + depth * 14;
  const isRenaming = renaming === node.path;
  const [draftName, setDraftName] = React.useState(node.name);
  React.useEffect(() => { if (isRenaming) setDraftName(node.name); }, [isRenaming]);
  const renameBox = (
    <input autoFocus value={draftName} onClick={(e) => e.stopPropagation()} onChange={(e) => setDraftName(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Enter") { onRename(node, draftName); setRenaming(null); } if (e.key === "Escape") setRenaming(null); }}
      onBlur={() => { onRename(node, draftName); setRenaming(null); }}
      style={{ flex: 1, minWidth: 0, height: 20, border: `1px solid ${RCA.accent}`, borderRadius: 3, padding: "0 5px", fontFamily: RCA.fMono, fontSize: 12, color: RCA.ink, outline: "none", background: RCA.white }}/>
  );
  if (node.type === "folder") {
    const o = open.has(node.path);
    return (
      <div>
        <div onClick={() => toggle(node.path)} onContextMenu={(e) => onCtx(e, node)} style={{ display: "flex", alignItems: "center", gap: 6, padding: `4px 12px 4px ${pad}px`, cursor: "pointer", userSelect: "none" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = RCA.paper2)} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <I name={o ? "chev_d" : "chev_r"} size={11} color={RCA.textPaperD2}/>
          <I name="folder" size={13} color={RCA.textPaperD}/>
          {isRenaming ? renameBox : <span style={{ fontSize: 13, color: RCA.textPaper, flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>}
          {!isRenaming && <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD2 }}>{countFiles(node)}</span>}
        </div>
        {o && node.children.map((c, i) => (
          <EvNode key={c.path + i} node={c} depth={depth + 1} open={open} toggle={toggle} view={view} onSelect={onSelect} renaming={renaming} setRenaming={setRenaming} onRename={onRename} onCtx={onCtx}/>
        ))}
      </div>
    );
  }
  const active = node.doc.view && node.doc.view === view;
  return (
    <div onClick={() => onSelect(node.doc)} onContextMenu={(e) => onCtx(e, node)} style={{ display: "flex", alignItems: "center", gap: 6, padding: `4px 12px 4px ${pad}px`, cursor: "pointer", background: active ? RCA.accentSoft + "55" : "transparent", borderLeft: active ? `2px solid ${RCA.accent}` : "2px solid transparent", marginLeft: -2, userSelect: "none" }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = RCA.paper2; }} onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}>
      <I name={node.doc.icon || docIcon(node.doc.kind)} size={13} color={active ? RCA.accentH : RCA.textPaperD}/>
      {isRenaming ? renameBox : <span style={{ fontSize: 13, color: active ? RCA.ink : RCA.textPaper, flex: 1, fontWeight: active ? 500 : 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>}
      {!isRenaming && node.doc.status === "indexing" && <Spinner/>}
      {!isRenaming && node.doc.status !== "indexing" && node.doc.scm && <span className="mono" style={{ fontSize: 10, fontWeight: 600, color: node.doc.scm === "M" ? RCA.warn : node.doc.scm === "A" ? RCA.ok : RCA.accent }}>{node.doc.scm}</span>}
    </div>
  );
}

// ---- evidence tree: stateful, uploadable, editable file manager ----
function EvidenceTree({ view, setView }) {
  const storeKey = "rca-inv-evidence";
  const seed = [
    { path: "data/reflow_zone3.csv", kind: "csv", scm: "M" },
    { path: "data/paste_press_log.csv", kind: "csv" },
    { path: "data/aoi_voids_w14.csv", kind: "csv", scm: "A" },
    { path: "photos-x-rays/board-A-0142.jpg", kind: "image" },
    { path: "photos-x-rays/xray-stack-1.tiff", kind: "image" },
    { path: "analyses/brief.md", kind: "md", icon: "file", view: "brief", scm: "M" },
    { path: "analyses/drift.ipynb", kind: "ipynb", icon: "chart", view: "spc", scm: "M" },
    { path: "analyses/pareto.ipynb", kind: "ipynb", icon: "pareto", view: "pareto" },
    { path: "analyses/fishbone.canvas", kind: "canvas", icon: "fishbone", view: "fishbone" },
    { path: "analyses/5-why.md", kind: "md", icon: "file", view: "fivewhy" },
    { path: "analyses/report.md", kind: "md", icon: "file", view: "report" },
  ].map((e) => ({ ...e, status: "indexed" }));
  const seedFolders = [".rca"];

  const [entries, setEntries] = React.useState(() => { try { const s = JSON.parse(localStorage.getItem(storeKey)); if (s && s.entries) return s.entries; } catch (e) {} return seed; });
  const [folders, setFolders] = React.useState(() => { try { const s = JSON.parse(localStorage.getItem(storeKey)); if (s && s.folders) return s.folders; } catch (e) {} return seedFolders; });
  React.useEffect(() => { try { localStorage.setItem(storeKey, JSON.stringify({ entries, folders })); } catch (e) {} }, [entries, folders]);

  const tree = React.useMemo(() => buildTree(entries, folders), [entries, folders]);
  const allFolders = React.useMemo(() => { const acc = []; const walk = (n) => n.children.forEach((c) => { if (c.type === "folder") { acc.push(c.path); walk(c); } }); walk(tree); return acc; }, [tree]);
  const [open, setOpen] = React.useState(() => new Set(allFolders));
  const [renaming, setRenaming] = React.useState(null);
  const [ctx, setCtx] = React.useState(null);
  const [dragActive, setDragActive] = React.useState(false);
  const [toast, setToast] = React.useState(null);
  const fileInputRef = React.useRef(null);
  const pendingFolder = React.useRef("");

  const toggle = (p) => setOpen((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });
  const flash = (m) => { setToast(m); setTimeout(() => setToast((t) => (t === m ? null : t)), 2200); };
  const uniquePath = (base) => { let p = base, i = 2; const has = (x) => entries.some((e) => e.path === x); const dot = base.lastIndexOf("."); while (has(p)) { p = dot > base.lastIndexOf("/") ? base.slice(0, dot) + "-" + i + base.slice(dot) : base + "-" + i; i++; } return p; };

  const addFiles = (fileList, folder) => {
    const arr = [...fileList]; if (!arr.length) return; let n = 0;
    arr.forEach((file) => {
      const rel = file.webkitRelativePath || file.name;
      const path = uniquePath((folder ? folder + "/" : "") + rel);
      setEntries((es) => [...es, { path, kind: extKind(file.name), scm: "A", status: "indexing" }]);
      setTimeout(() => setEntries((es) => es.map((e) => e.path === path ? { ...e, status: "indexed" } : e)), 1500);
      if (rel.includes("/")) { const parts = ((folder ? folder + "/" : "") + rel).split("/").slice(0, -1); const acc = []; parts.forEach((_, i) => acc.push(parts.slice(0, i + 1).join("/"))); setFolders((f) => [...new Set([...f, ...acc])]); }
      n++;
    });
    if (folder) setOpen((s) => new Set([...s, folder]));
    flash(n + (n === 1 ? " file added \u00b7 indexing\u2026" : " files added \u00b7 indexing\u2026"));
  };

  const onRename = (node, raw) => {
    const name = (raw || "").trim(); if (!name || name === node.name) return;
    if (node.type === "file") { const parts = node.path.split("/"); parts[parts.length - 1] = name; const np = parts.join("/"); setEntries((es) => es.map((e) => e.path === node.path ? { ...e, path: np, kind: extKind(name) } : e)); }
    else { const parts = node.path.split("/"); parts[parts.length - 1] = name; const np = parts.join("/"); setEntries((es) => es.map((e) => e.path.startsWith(node.path + "/") ? { ...e, path: np + e.path.slice(node.path.length) } : e)); setFolders((f) => f.map((p) => p === node.path ? np : p.startsWith(node.path + "/") ? np + p.slice(node.path.length) : p)); }
  };
  const onDelete = (node) => {
    if (node.type === "file") setEntries((es) => es.filter((e) => e.path !== node.path));
    else { setEntries((es) => es.filter((e) => !(e.path === node.path || e.path.startsWith(node.path + "/")))); setFolders((f) => f.filter((p) => !(p === node.path || p.startsWith(node.path + "/")))); }
    flash("Deleted " + node.name);
  };
  const onDownload = (node) => { const blob = new Blob([""], { type: "text/plain" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = node.name; a.click(); URL.revokeObjectURL(url); };
  const newFile = () => { const path = uniquePath("analyses/untitled.md"); setEntries((es) => [...es, { path, kind: "md", icon: "file", scm: "A", status: "indexed" }]); setOpen((s) => new Set([...s, "analyses"])); setTimeout(() => setRenaming(path), 60); };
  const newFolder = () => { let name = "new-folder", i = 2; while (folders.includes(name) || tree.children.some((c) => c.type === "folder" && c.name === name)) name = "new-folder-" + (i++); setFolders((f) => [...f, name]); setOpen((s) => new Set([...s, name])); setTimeout(() => setRenaming(name), 60); };
  const onSelect = (doc) => { if (doc.view) setView(doc.view); };
  const openCtx = (e, node) => { e.preventDefault(); e.stopPropagation(); setCtx({ x: e.clientX, y: e.clientY, node }); };
  const ctxItems = (node) => node.type === "file" ? [
    { icon: "pencil", label: "Rename", onClick: () => setRenaming(node.path) },
    { icon: "download", label: "Download", onClick: () => onDownload(node) },
    { sep: true },
    { icon: "trash", label: "Delete", danger: true, onClick: () => onDelete(node) },
  ] : [
    { icon: "file_plus", label: "New file here", onClick: () => { const path = uniquePath(node.path + "/untitled.md"); setEntries((es) => [...es, { path, kind: "md", icon: "file", scm: "A", status: "indexed" }]); setOpen((s) => new Set([...s, node.path])); setTimeout(() => setRenaming(path), 60); } },
    { icon: "pencil", label: "Rename", onClick: () => setRenaming(node.path) },
    { sep: true },
    { icon: "trash", label: "Delete folder", danger: true, onClick: () => onDelete(node) },
  ];

  return (
    <div style={{ padding: "4px 0", position: "relative" }}
      onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragActive(false); }}
      onDrop={(e) => { e.preventDefault(); setDragActive(false); if (e.dataTransfer.files && e.dataTransfer.files.length) addFiles(e.dataTransfer.files, ""); }}>
      <style>{`@keyframes rcaspin{to{transform:rotate(360deg)}}`}</style>
      {ctx && <CtxMenu x={ctx.x} y={ctx.y} items={ctxItems(ctx.node)} onClose={() => setCtx(null)}/>}
      <input ref={fileInputRef} type="file" multiple style={{ display: "none" }} onChange={(e) => { addFiles(e.target.files, pendingFolder.current); pendingFolder.current = ""; e.target.value = ""; }}/>
      <div style={{ padding: "4px 8px 4px 16px", display: "flex", alignItems: "center", gap: 2 }}>
        <I name="chev_d" size={10} color={RCA.textPaperD2}/>
        <CapsLabel style={{ fontSize: 10 }}>Investigation files</CapsLabel>
        <div style={{ flex: 1 }}/>
        <IconBtn name="file_plus" title="New file" onClick={newFile}/>
        <IconBtn name="folder_plus" title="New folder" onClick={newFolder}/>
        <IconBtn name="upload" title="Upload files" onClick={() => { pendingFolder.current = ""; fileInputRef.current && fileInputRef.current.click(); }}/>
        <IconBtn name="collapse" title="Collapse all" onClick={() => setOpen(new Set())}/>
      </div>
      <div>
        {tree.children.map((c, i) => (
          <EvNode key={c.path + i} node={c} depth={0} open={open} toggle={toggle} view={view} onSelect={onSelect} renaming={renaming} setRenaming={setRenaming} onRename={onRename} onCtx={openCtx}/>
        ))}
      </div>
      {dragActive && (
        <div style={{ position: "absolute", inset: 4, background: "rgba(240,80,46,0.06)", border: `2px dashed ${RCA.accent}`, borderRadius: 8, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6, pointerEvents: "none", zIndex: 5 }}>
          <I name="upload" size={22} color={RCA.accent}/>
          <div style={{ fontSize: 12, fontWeight: 600, color: RCA.accentH }}>Drop to add evidence</div>
        </div>
      )}
      {toast && (
        <div style={{ position: "fixed", bottom: 44, left: 320, zIndex: 60, display: "flex", alignItems: "center", gap: 9, padding: "9px 14px", background: RCA.ink, color: RCA.white, borderRadius: 8, fontSize: 12, boxShadow: "0 8px 28px rgba(22,24,29,0.22)" }}>
          <span style={{ width: 16, height: 16, borderRadius: 4, background: RCA.accent, display: "inline-flex", alignItems: "center", justifyContent: "center" }}><RCAMark size={11} color={RCA.white}/></span>
          {toast}
        </div>
      )}
    </div>
  );
}

function OutlineRow({ label, active }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 16px", color: active ? RCA.ink : RCA.textPaperD, fontSize: 12 }}>
      <span style={{ width: 4, height: 4, borderRadius: "50%", background: active ? RCA.accent : RCA.textPaperD2 }}/>
      <span style={{ fontWeight: active ? 600 : 400 }}>{label}</span>
    </div>
  );
}

function Tab({ icon, label, active, modified }) {
  return (
    <div style={{
      padding: "0 14px", display: "flex", alignItems: "center", gap: 8,
      borderRight: `1px solid ${RCA.paper3}`,
      background: active ? RCA.white : "transparent",
      borderTop: active ? `2px solid ${RCA.accent}` : `2px solid transparent`,
      cursor: "pointer",
      whiteSpace: "nowrap", flexShrink: 0,
    }}>
      <I name={icon} size={13} color={active ? RCA.accentH : RCA.textPaperD}/>
      <span style={{ fontSize: 13, color: active ? RCA.ink : RCA.textPaperD, fontWeight: active ? 500 : 400, whiteSpace: "nowrap" }}>{label}</span>
      {modified
        ? <span style={{ width: 7, height: 7, borderRadius: "50%", background: RCA.warn }}/>
        : <I name="x" size={11} color={RCA.textPaperD2}/>
      }
    </div>
  );
}

function NotebookCellRCA({ n, kind, status, duration, active, children }) {
  const ringColor = active ? RCA.accent : RCA.paper3;
  return (
    <div style={{
      display: "flex", gap: 14, marginBottom: 16, position: "relative",
    }}>
      {/* run gutter */}
      <div style={{ width: 36, paddingTop: 12, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: 28, height: 28, border: `1px solid ${ringColor}`, borderRadius: "50%", background: active ? RCA.accentSoft : "transparent", cursor: "pointer" }}>
          <I name="play" size={12} color={active ? RCA.accentH : RCA.textPaperD}/>
        </div>
        <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD2 }}>[{n}]</span>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          background: RCA.white, border: `1px solid ${active ? RCA.accent : RCA.paper3}`,
          borderRadius: 8, overflow: "hidden",
        }}>
          {/* cell header */}
          <div style={{ padding: "6px 12px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", gap: 10, background: active ? RCA.accentSoft + "55" : "transparent" }}>
            <RcaChip tone={kind === "md" ? "outline" : "default"}>{kind === "md" ? "markdown" : kind}</RcaChip>
            {status === "ok" && <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD }}>● ran in {duration}</span>}
            {status === "running" && <span className="mono" style={{ fontSize: 11, color: RCA.accent }}>● running…</span>}
            <div style={{ flex: 1 }}/>
            <Btn size="sm" variant="ghost" icon={<I name="sparkle" size={12}/>}>Explain</Btn>
            <Btn size="sm" variant="ghost" icon={<I name="dots_h" size={12}/>}/>
          </div>
          {/* cell body */}
          <div style={{ padding: kind === "md" ? 16 : 14 }}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

function OutputCell({ label, children }) {
  return (
    <div style={{ display: "flex", gap: 14, marginBottom: 16 }}>
      <div style={{ width: 36, paddingTop: 4, textAlign: "right" }}>
        <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD2 }}>out</span>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, overflow: "hidden" }}>
          <div style={{ padding: "5px 12px", borderBottom: `1px dashed ${RCA.paper3}`, fontSize: 11, color: RCA.textPaperD, fontFamily: RCA.fMono }}>{label}</div>
          {children}
        </div>
      </div>
    </div>
  );
}

function Callout({ icon, tone, title, body }) {
  const tones = {
    accent: { bg: RCA.accentSoft, fg: RCA.accentH, border: RCA.accent },
    info: { bg: "rgba(45,108,201,.08)", fg: RCA.info, border: RCA.info },
  };
  const t = tones[tone] || tones.info;
  return (
    <div style={{ display: "flex", gap: 14, marginBottom: 16 }}>
      <div style={{ width: 36, paddingTop: 4, textAlign: "right" }}>
        <I name={icon} size={16} color={t.fg}/>
      </div>
      <div style={{ flex: 1, background: t.bg, border: `1px solid ${t.border}33`, borderLeft: `3px solid ${t.border}`, borderRadius: 6, padding: "10px 14px" }}>
        <div className="caps" style={{ color: t.fg, marginBottom: 4 }}>{title}</div>
        <div style={{ fontSize: 13, color: RCA.ink, lineHeight: 1.5 }}>{body}</div>
      </div>
    </div>
  );
}

// SPC chart with annotation
function ChartSPC() {
  const W = 720, H = 220, PAD = { l: 44, r: 28, t: 20, b: 28 };
  const points = [245, 244.9, 245.1, 244.8, 245, 245.1, 244.9, 245, 244.7, 244.5, 244.2, 244, 243.5, 243, 242.5, 242, 241.8, 241.9, 241.8, 241.7, 241.8, 241.9, 242, 241.8, 241.7, 241.8, 241.8, 241.9, 241.8, 241.7];
  const voids = [1.3, 1.4, 1.3, 1.5, 1.4, 1.3, 1.4, 1.5, 1.5, 1.6, 1.8, 2.1, 2.6, 2.8, 3.0, 3.1, 3.2, 3.2, 3.1, 3.2, 3.2, 3.1, 3.2, 3.2, 3.1, 3.2, 3.2, 3.1, 3.2, 3.2];
  const x = (i) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
  const yT = (t) => PAD.t + (1 - (t - 240) / 6) * (H - PAD.t - PAD.b);
  const yV = (v) => PAD.t + (1 - (v - 0) / 4) * (H - PAD.t - PAD.b);
  const pathT = points.map((t, i) => `${i ? "L" : "M"} ${x(i)} ${yT(t)}`).join(" ");
  const pathV = voids.map((v, i) => `${i ? "L" : "M"} ${x(i)} ${yV(v)}`).join(" ");

  return (
    <div style={{ padding: "14px 18px" }}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
        {/* grid */}
        {[0, 1, 2, 3].map(i => (
          <line key={i} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + i * ((H - PAD.t - PAD.b) / 3)} y2={PAD.t + i * ((H - PAD.t - PAD.b) / 3)} stroke={RCA.paper3} strokeDasharray="2 4"/>
        ))}
        {/* set-point line */}
        <line x1={PAD.l} x2={W - PAD.r} y1={yT(245)} y2={yT(245)} stroke={RCA.ok} strokeWidth="1" strokeDasharray="4 4"/>
        <text x={W - PAD.r - 4} y={yT(245) - 4} fontFamily={RCA.fMono} fontSize="10" fill={RCA.ok} textAnchor="end">set-point 245°C</text>
        {/* spec lower */}
        <line x1={PAD.l} x2={W - PAD.r} y1={yT(243)} y2={yT(243)} stroke={RCA.warn} strokeWidth="1" strokeDasharray="3 3"/>
        <text x={W - PAD.r - 4} y={yT(243) - 4} fontFamily={RCA.fMono} fontSize="10" fill={RCA.warn} textAnchor="end">LSL 243°C</text>
        {/* temp curve */}
        <path d={pathT} fill="none" stroke={RCA.ink} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
        {/* void curve (orange) */}
        <path d={pathV} fill="none" stroke={RCA.accent} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
        {/* annotation */}
        <line x1={x(11)} x2={x(11)} y1={PAD.t} y2={H - PAD.b} stroke={RCA.accent} strokeWidth="1" strokeDasharray="2 3"/>
        <circle cx={x(11)} cy={yT(244)} r="3" fill={RCA.accent}/>
        <rect x={x(11) + 6} y={PAD.t + 6} width="120" height="28" rx="4" fill={RCA.ink}/>
        <text x={x(11) + 12} y={PAD.t + 19} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textDark}>08-14 13:30</text>
        <text x={x(11) + 12} y={PAD.t + 30} fontFamily={RCA.fMono} fontSize="9" fill={RCA.textDarkD}>drift onset →</text>
        {/* y-axis labels */}
        <text x={PAD.l - 8} y={yT(246)} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD} textAnchor="end">246</text>
        <text x={PAD.l - 8} y={yT(244)} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD} textAnchor="end">244</text>
        <text x={PAD.l - 8} y={yT(242)} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD} textAnchor="end">242</text>
        <text x={PAD.l - 8} y={yT(240)} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD} textAnchor="end">240°C</text>
        <text x={W - PAD.r + 6} y={yV(1)} fontFamily={RCA.fMono} fontSize="10" fill={RCA.accent}>1%</text>
        <text x={W - PAD.r + 6} y={yV(3)} fontFamily={RCA.fMono} fontSize="10" fill={RCA.accent}>3%</text>
        {/* x-axis labels */}
        <text x={PAD.l} y={H - 8} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD}>08-10</text>
        <text x={x(15)} y={H - 8} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD}>08-14</text>
        <text x={W - PAD.r} y={H - 8} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD} textAnchor="end">08-18</text>
      </svg>
      <div style={{ display: "flex", gap: 14, padding: "8px 0 0", borderTop: `1px solid ${RCA.paper3}`, marginTop: 4 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: RCA.textPaperD, fontFamily: RCA.fMono }}>
          <span style={{ width: 12, height: 2, background: RCA.ink }}/>zone-3 actual
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: RCA.textPaperD, fontFamily: RCA.fMono }}>
          <span style={{ width: 12, height: 2, background: RCA.accent }}/>void rate · MX-7
        </span>
      </div>
    </div>
  );
}

function AgentLogLine({ t, kind, children }) {
  const tones = {
    plan: { c: RCA.info, tag: "plan" },
    tool: { c: RCA.textPaperD, tag: "tool" },
    insight: { c: RCA.accent, tag: "insight" },
    running: { c: RCA.warn, tag: "..." },
  };
  const k = tones[kind] || tones.tool;
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
      <span style={{ color: RCA.textPaperD2, width: 64 }}>{t}</span>
      <span style={{ color: k.c, width: 60 }}>{k.tag}</span>
      <span style={{ color: RCA.textPaper, flex: 1 }}>{children}</span>
    </div>
  );
}

function AgentPanel({ onView, currentView }) {
  const [messages, setMessages] = React.useState([
    { role: "user", who: "AC", time: "14:32", text: "Voids spiked 2.3× on Line 3 since 08-14 14:00. Find the root cause and propose actions.", attach: ["@ brief.md"] },
    { role: "agent", kind: "plan", text: "Plan", plan: [
      "Pull SPC for reflow zone-3 (14d) + AOI void timeline",
      "Check for upstream sensor drift",
      "Correlate top candidates with void rate",
      "→ Examine Pareto of failure modes",
      "Draft a 5-why on the leading hypothesis",
      "Propose corrective + containment actions",
    ], currentStep: 3 },
    { role: "tool", name: "spc.read", args: "reflow.zone3 · 14d", result: "20,160 samples" },
    { role: "tool", name: "defects.aoi", args: "MX-7 · lot 25-W14", result: "412 records · 3.2%" },
    { role: "agent", compact: true, text: <><strong>Observation.</strong> Zone-3 actual drifted from 245.0°C to 241.8°C between 08-14 13:30 and 14:00 — 30 min before the void rate climbed. Drift sustained across 4 shifts; no controller retune was logged.</> },
    { role: "tool", name: "correlate.find", args: "target=void_rate · 4 candidates", running: true },
  ]);
  const [pending, setPending] = React.useState(false);

  // Suggestions adapt to current view
  const SUGGESTIONS = {
    brief:    [["Show SPC analysis","spc"], ["Run Pareto","pareto"], ["Sketch a fishbone","fishbone"]],
    spc:      [["Run Pareto","pareto"], ["Sketch a fishbone","fishbone"], ["Draft 5-Why","fivewhy"]],
    pareto:   [["Sketch a fishbone","fishbone"], ["Draft 5-Why","fivewhy"], ["Draft report","report"]],
    fishbone: [["Draft 5-Why","fivewhy"], ["Draft report","report"], ["Re-check correlations",null]],
    fivewhy:  [["Draft report","report"], ["Propose containment",null], ["Add preventive action",null]],
    report:   [["Submit for review",null], ["Export PDF",null], ["Open new investigation",null]],
  };
  const tips = SUGGESTIONS[currentView] || SUGGESTIONS.brief;

  const cannedReplies = {
    spc:      "Here's the SPC overlay. Zone-3 drift clearly precedes the void spike by ~30 min. Opening that view now.",
    pareto:   "Pareto built across 14d of MX-7 defects — three modes account for 78%. Top contributor is BGA-pad voids, consistent with the zone-3 hypothesis.",
    fishbone: "Drafted a 6M fishbone. Branches with current evidence are flagged orange. Want me to rule out the other branches?",
    fivewhy:  "Drafted a 5-Why chain — root sits in change-control: throughput aggregations aren't currently flagged. Have a look.",
    report:   "Drafted an 8D report. Containment + corrective + preventive all populated. Review and submit.",
  };

  const handle = (label, targetView) => {
    setMessages(m => [...m, { role: "user", who: "AC", time: "now", text: label }]);
    setPending(true);
    setTimeout(() => {
      if (targetView) onView && onView(targetView);
      setMessages(m => [...m, { role: "agent", compact: true, text: cannedReplies[targetView] || "On it." }]);
      setPending(false);
    }, 700);
  };

  return (
    <aside style={{ width: 380, borderLeft: `1px solid ${RCA.paper3}`, display: "flex", flexDirection: "column", overflow: "hidden", background: RCA.paper }}>
      <div style={{ padding: "12px 16px", display: "flex", alignItems: "center", gap: 10, borderBottom: `1px solid ${RCA.paper3}` }}>
        <RCAMark size={18}/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>RCA Agent</div>
          <div style={{ fontSize: 11, color: RCA.textPaperD }}>investigating · 4/6 steps</div>
        </div>
        <RcaChip tone="accentSolid" icon={<I name="sparkle" size={10}/>}>{pending ? "thinking" : "running"}</RcaChip>
      </div>

      <div style={{ padding: "10px 16px 12px", borderBottom: `1px solid ${RCA.paper3}` }}>
        <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
          {[1,1,1,0.6,0,0].map((v, i) => (
            <div key={i} style={{ flex: 1, height: 3, borderRadius: 2, background: v === 1 ? RCA.accent : v > 0 ? RCA.accentSoft : RCA.paper3, position: "relative" }}>
              {v === 0.6 && <div style={{ position: "absolute", inset: 0, width: "60%", background: RCA.accent, borderRadius: 2 }}/>}
            </div>
          ))}
        </div>
        <div className="mono" style={{ fontSize: 11, color: RCA.textPaperD }}>step 4 · finding correlations</div>
      </div>

      <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 14 }}>
        {messages.map((m, i) => {
          if (m.role === "user") return (
            <MsgUser key={i} who={m.who} time={m.time}>
              {m.text}
              {m.attach && (
                <div style={{ display: "flex", gap: 4, marginTop: 8, flexWrap: "wrap" }}>
                  {m.attach.map((a, j) => <RcaChip key={j} tone="default" icon={<I name="file" size={10}/>}>{a}</RcaChip>)}
                </div>
              )}
            </MsgUser>
          );
          if (m.role === "tool") return <ToolCall key={i} name={m.name} args={m.args} result={m.result} running={m.running}/>;
          if (m.role === "agent" && m.kind === "plan") return (
            <MsgAgent key={i}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{m.text}</div>
              <ol style={{ margin: 0, paddingLeft: 16, fontSize: 13, color: RCA.textPaper, lineHeight: 1.55 }}>
                {m.plan.map((p, j) => <li key={j} style={{ color: j === m.currentStep ? RCA.accent : RCA.textPaper, fontWeight: j === m.currentStep ? 600 : 400 }}>{p}</li>)}
              </ol>
            </MsgAgent>
          );
          return <MsgAgent key={i} compact={m.compact} tentative={m.tentative}>{m.text}</MsgAgent>;
        })}
        {pending && <MsgAgent compact tentative><span className="mono" style={{ color: RCA.textPaperD }}>…thinking</span></MsgAgent>}
      </div>

      <div style={{ padding: "10px 14px 0", borderTop: `1px solid ${RCA.paper3}` }}>
        <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
          {tips.map(([label, v], i) => (
            <div key={i} onClick={() => handle(label, v)} style={{
              padding: "5px 9px", border: `1px solid ${RCA.paper3}`, borderRadius: 14,
              fontSize: 11, fontFamily: RCA.fBody, color: RCA.textPaper, background: RCA.white,
              cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5,
            }}>
              <I name="sparkle" size={11} color={RCA.accent}/>{label}
            </div>
          ))}
        </div>
      </div>
      <div style={{ padding: "0 14px 14px" }}>
        <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, padding: 10 }}>
          <div style={{ fontSize: 13, color: RCA.textPaperD2, marginBottom: 8 }}>Ask anything, or pick a chip above…</div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Btn size="sm" variant="ghost" icon={<I name="plus" size={13}/>}>Attach</Btn>
            <div style={{ flex: 1 }}/>
            <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>⌘↵</span>
            <Btn size="sm" variant="primary" icon={<I name="arrow_r" size={13}/>}>Send</Btn>
          </div>
        </div>
      </div>
    </aside>
  );
}

function MsgUser({ children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Avatar name="AC" size={20}/>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Alice</span>
        <span style={{ fontSize: 11, color: RCA.textPaperD2 }}>14:32</span>
      </div>
      <div style={{ fontSize: 13, color: RCA.textPaper, lineHeight: 1.5, paddingLeft: 28 }}>{children}</div>
    </div>
  );
}

function MsgAgent({ children, compact, tentative }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 20, height: 20, borderRadius: 4, background: RCA.ink, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <RCAMark size={14} color={RCA.textDark}/>
        </div>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Agent</span>
        {tentative && <span style={{ fontSize: 11, color: RCA.accent }}>● running</span>}
      </div>
      <div style={{ fontSize: 13, color: RCA.textPaper, lineHeight: 1.55, paddingLeft: 28 }}>{children}</div>
    </div>
  );
}

function ToolCall({ name, args, result, running }) {
  return (
    <div style={{ marginLeft: 28, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6, padding: "8px 10px", display: "flex", alignItems: "center", gap: 10 }}>
      <I name={running ? "play" : "check"} size={13} color={running ? RCA.accent : RCA.ok}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="mono" style={{ fontSize: 12, color: RCA.ink }}>{name}<span style={{ color: RCA.textPaperD }}>({args})</span></div>
        {result && <div className="mono" style={{ fontSize: 11, color: RCA.textPaperD }}>→ {result}</div>}
        {running && <div className="mono" style={{ fontSize: 11, color: RCA.accent }}>running…</div>}
      </div>
      <I name="chev_d" size={12} color={RCA.textPaperD2}/>
    </div>
  );
}

Object.assign(window, { InvestigationRCA, INV_W, INV_H });
