// Knowledge Wiki — AI-maintained, read-only, navigable knowledge base for a collection.
// Exposes: WikiBrowser (4 states), RetrievalToggles, WikiSearchRow, WikiBadge
// Depends on RCA, Btn, RcaChip, I, CapsLabel, Avatar from rca/system.jsx

// ============================================================
// Sample wiki content — a small interlinked knowledge base
// ============================================================
const WIKI_TREE = [
  { group: null,        pages: [{ path: "/index.md", title: "Index", key: "index" }] },
  { group: "Entities",  pages: [
    { path: "/entities/reflow-zone-3.md", title: "Reflow Zone 3", key: "reflow-zone-3" },
    { path: "/entities/oven-profile.md",  title: "Oven Profile",  key: "oven-profile" },
    { path: "/entities/mx-7-board.md",    title: "MX-7 Board",    key: "mx-7-board" },
    { path: "/entities/pid-controller.md",title: "PID Controller",key: "pid-controller" },
  ]},
  { group: "Concepts",  pages: [
    { path: "/concepts/voiding.md",        title: "Voiding",          key: "voiding" },
    { path: "/concepts/thermal-drift.md",  title: "Thermal Drift",    key: "thermal-drift" },
    { path: "/concepts/change-control.md", title: "Change Control",   key: "change-control" },
  ]},
];

// markdown-ish blocks per page. Body is an array of block objects so we can
// render wikilinks + sources without a full markdown parser.
const WIKI_PAGES = {
  "index": {
    title: "Reflow Process",
    eyebrow: "Index",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["This collection covers the reflow soldering process for surface-mount assembly — the oven stages, the thermal profile, and the defects that arise when temperatures drift out of band. Start with an entity or a concept below."] },
      { t: "h2", c: "Entities" },
      { t: "ul", c: [
        [{ link: "reflow-zone-3", label: "Reflow Zone 3" }, " — the hottest oven stage, where most voiding originates."],
        [{ link: "oven-profile", label: "Oven Profile" }, " — the temperature curve a board travels through."],
        [{ link: "mx-7-board", label: "MX-7 Board" }, " — the primary product assembled on this line."],
        [{ link: "pid-controller", label: "PID Controller" }, " — holds each zone at its set-point."],
      ]},
      { t: "h2", c: "Concepts" },
      { t: "ul", c: [
        [{ link: "voiding", label: "Voiding" }, " — gas pockets trapped in a solder joint."],
        [{ link: "thermal-drift", label: "Thermal Drift" }, " — slow departure of actual temperature from set-point."],
        [{ link: "change-control", label: "Change Control" }, " — the gate that decides when a process change needs review."],
      ]},
    ],
    sources: ["reflow-spec.pdf", "smt-line-overview.md", "qual-report-25w14.md"],
  },
  "reflow-zone-3": {
    title: "Reflow Zone 3",
    eyebrow: "Entity · oven stage",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["Zone 3 is the hottest stage of the reflow oven, where the solder paste reaches peak reflow temperature. Because it runs closest to the process limit, it is the stage most sensitive to ", { link: "thermal-drift", label: "thermal drift" }, ". Most ", { link: "voiding", label: "voiding" }, " defects on the ", { link: "mx-7-board", label: "MX-7 board" }, " originate here."] },
      { t: "h2", c: "Key facts" },
      { t: "ul", c: [
        ["Set-point: 245 °C (per spec)."],
        ["Lower spec limit: 243 °C — below this, voiding rises sharply."],
        ["Held by the ", { link: "pid-controller", label: "PID controller" }, ", tuned for a given throughput."],
        ["Part of the overall ", { link: "oven-profile", label: "oven profile" }, " (zones 1–7)."],
      ]},
      { t: "h2", c: "Why it matters" },
      { t: "p", c: ["When throughput rises without a corresponding re-tune, the controller can no longer hold the set-point and the zone drifts cool. A drift of even 3 °C has been enough to push the void rate from ~1.4% to >3%."] },
    ],
    sources: ["reflow-spec.pdf", "qual-report-25w14.md"],
  },
  "oven-profile": {
    title: "Oven Profile",
    eyebrow: "Entity · process",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["The oven profile is the temperature-versus-time curve a board experiences as it travels through the reflow oven's zones. It has four regions: preheat, soak, reflow (peak), and cooling. ", { link: "reflow-zone-3", label: "Reflow Zone 3" }, " sits at the reflow peak."] },
      { t: "h2", c: "Regions" },
      { t: "ul", c: [
        ["Preheat — ramp the board gently to avoid thermal shock."],
        ["Soak — even out temperature across the board."],
        ["Reflow — peak above the solder's melting point."],
        ["Cooling — solidify the joints."],
      ]},
      { t: "p", c: ["A profile is validated for a given board and throughput. Changing throughput effectively changes dwell time in each zone — see ", { link: "change-control", label: "change control" }, "."] },
    ],
    sources: ["reflow-spec.pdf", "profile-validation-mx7.pdf"],
  },
  "mx-7-board": {
    title: "MX-7 Board",
    eyebrow: "Entity · product",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["The MX-7 is the primary printed-circuit board assembled on this line. It carries several BGA packages whose solder joints are the ones most prone to ", { link: "voiding", label: "voiding" }, "."] },
      { t: "h2", c: "Key facts" },
      { t: "ul", c: [
        ["Void-rate baseline: ≤ 1.8% (investigate above 2.4% rolling)."],
        ["Assembled through the standard 7-zone ", { link: "oven-profile", label: "oven profile" }, "."],
        ["Most sensitive joints: corner BGA pads."],
      ]},
    ],
    sources: ["mx7-datasheet.pdf", "qual-report-25w14.md"],
  },
  "pid-controller": {
    title: "PID Controller",
    eyebrow: "Entity · equipment",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["Each oven zone is held at its set-point by a PID controller. Its gains are tuned for a particular throughput; when throughput changes materially, the same gains may no longer hold the set-point, producing ", { link: "thermal-drift", label: "thermal drift" }, "."] },
      { t: "h2", c: "Tuning" },
      { t: "p", c: ["Re-tuning is required when throughput aggregates beyond roughly 8% of nominal — a trigger that historically was not captured by ", { link: "change-control", label: "change control" }, "."] },
    ],
    sources: ["reflow-spec.pdf", "controller-manual.pdf"],
  },
  "voiding": {
    title: "Voiding",
    eyebrow: "Concept · defect",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["Voiding is the formation of gas pockets inside a solder joint during reflow. Measured as area-fraction by AOI or X-ray and expressed as a percentage. Excess voiding weakens the joint and reduces thermal conduction."] },
      { t: "h2", c: "Acceptance" },
      { t: "ul", c: [
        ["Class 2 (general): ≤ 25% voiding by area."],
        ["Class 3 (high-reliability): ≤ 9%."],
        [{ link: "mx-7-board", label: "MX-7" }, " baseline: ~1.4%; alarm on the ", { link: "thermal-drift", label: "drift" }, ", not the absolute value."],
      ]},
      { t: "p", c: ["On this line, voiding concentrates at ", { link: "reflow-zone-3", label: "Reflow Zone 3" }, " and rises sharply when the zone drifts below its lower spec limit."] },
    ],
    sources: ["ipc-a-610-extract.pdf", "qual-report-25w14.md"],
  },
  "thermal-drift": {
    title: "Thermal Drift",
    eyebrow: "Concept · failure mode",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["Thermal drift is the slow departure of a zone's actual temperature from its set-point, usually over a shift rather than instantly. It is dangerous precisely because it is gradual — absolute-threshold alarms can miss it."] },
      { t: "h2", c: "Typical cause" },
      { t: "p", c: ["A throughput increase that is not matched by a ", { link: "pid-controller", label: "PID" }, " re-tune. The controller falls behind and ", { link: "reflow-zone-3", label: "Zone 3" }, " runs cool, lifting the ", { link: "voiding", label: "void" }, " rate."] },
      { t: "h2", c: "Detection" },
      { t: "p", c: ["Use a baseline-anchored SPC rule (actual vs set-point delta), not a fixed threshold. Alarm when the delta exceeds 2 °C over a 15-minute window."] },
    ],
    sources: ["spc-playbook.md", "qual-report-25w14.md"],
  },
  "change-control": {
    title: "Change Control",
    eyebrow: "Concept · governance",
    updated: "4 min ago",
    body: [
      { t: "p", c: ["Change control is the gate that decides whether a process change requires formal review before it takes effect. Historically the matrix listed material and line-speed changes but not throughput aggregation — a gap that allowed an un-reviewed change to cause ", { link: "thermal-drift", label: "thermal drift" }, "."] },
      { t: "h2", c: "The gap" },
      { t: "p", c: ["Because throughput was not a listed trigger, a cumulative increase never prompted a ", { link: "pid-controller", label: "PID" }, " re-tune. The recommended fix is to add throughput aggregation above 8% as a trigger."] },
    ],
    sources: ["change-control-matrix.xlsx", "qual-report-25w14.md"],
  },
};

// ============================================================
// Markdown-ish renderer (prose + wikilinks)
// ============================================================
function WikiProse({ blocks, onNavigate }) {
  const renderInline = (parts, ki) => {
    if (typeof parts === "string") return parts;
    return parts.map((p, i) => {
      if (typeof p === "string") return <React.Fragment key={i}>{p}</React.Fragment>;
      if (p.link) return (
        <a key={i} onClick={(e) => { e.preventDefault(); onNavigate(p.link); }} href="#"
          style={{ color: RCA.accentH, textDecoration: "underline", textDecorationColor: RCA.accentSoft, textUnderlineOffset: "2px", textDecorationThickness: "1.5px", cursor: "pointer", fontWeight: 500 }}>
          {p.label}
        </a>
      );
      return null;
    });
  };
  return (
    <div style={{ fontFamily: RCA.fBody, color: RCA.textPaper }}>
      {blocks.map((b, i) => {
        if (b.t === "p") return <p key={i} style={{ fontSize: 15, lineHeight: 1.65, margin: "0 0 16px", maxWidth: 680 }}>{renderInline(b.c)}</p>;
        if (b.t === "h2") return <h2 key={i} className="display" style={{ fontSize: 18, margin: "26px 0 12px", letterSpacing: "-0.01em" }}>{b.c}</h2>;
        if (b.t === "ul") return (
          <ul key={i} style={{ margin: "0 0 16px", paddingLeft: 0, listStyle: "none", maxWidth: 680, display: "flex", flexDirection: "column", gap: 8 }}>
            {b.c.map((li, j) => (
              <li key={j} style={{ fontSize: 15, lineHeight: 1.6, paddingLeft: 20, position: "relative" }}>
                <span style={{ position: "absolute", left: 4, top: 9, width: 5, height: 5, borderRadius: "50%", background: RCA.accent }}/>
                {renderInline(li)}
              </li>
            ))}
          </ul>
        );
        return null;
      })}
    </div>
  );
}

// ============================================================
// WIKI BROWSER — file-tree + editor, same shell as Documents.
// Wiki pages are .md files; opening one edits it directly (the agent's
// generated text). Drop docs to regenerate. Persists per collection.
// state: "ready" | "empty" | "building" | "disabled"
// ============================================================
function wikiInline(c) {
  if (typeof c === "string") return c;
  if (!Array.isArray(c)) return "";
  return c.map((x) => typeof x === "string" ? x : (x && x.label ? x.label : "")).join("");
}
function wikiPageMd(pg) {
  let md = "# " + pg.title + "\n\n";
  (pg.body || []).forEach((b) => {
    if (b.t === "p") md += wikiInline(b.c) + "\n\n";
    else if (b.t === "h2") md += "## " + b.c + "\n\n";
    else if (b.t === "ul") md += (b.c || []).map((li) => "- " + wikiInline(li)).join("\n") + "\n\n";
  });
  return md.trim() + "\n";
}
function buildWikiDocs() {
  const docs = [];
  WIKI_TREE.forEach((grp) => grp.pages.forEach((pg) => {
    const data = WIKI_PAGES[pg.key];
    if (!data) return;
    const md = wikiPageMd(data);
    docs.push({ path: pg.path.replace(/^\//, ""), kind: "md", content: md, chunks: (data.sources || []).length + 2, cited: 0, updated: "2026-06-12", by: "Agent", size: md.length + " B" });
  }));
  return docs;
}
function buildWikiSources() {
  const map = {};
  WIKI_TREE.forEach((grp) => grp.pages.forEach((pg) => {
    const data = WIKI_PAGES[pg.key];
    if (data) map[pg.path.replace(/^\//, "")] = data.sources || [];
  }));
  return map;
}

function WikiBrowser({ state = "ready", collectionName = "Reflow process", onOpenSource, embedded, isAuto }) {
  const [liveState, setLiveState] = React.useState(state);
  const [lastUpdated, setLastUpdated] = React.useState("4 min ago");
  React.useEffect(() => { setLiveState(state); }, [state]);

  const wikiDocs = React.useMemo(() => buildWikiDocs(), []);
  const sourcesByPath = React.useMemo(() => buildWikiSources(), []);

  const regenerate = () => {
    setLiveState("building");
    setTimeout(() => { setLiveState("ready"); setLastUpdated("just now"); }, 2600);
  };

  const wrap = (children) => (
    <div className="rca" style={{ height: embedded ? "100%" : 760, display: "flex", flexDirection: "column", background: RCA.paper, border: embedded ? "none" : `1px solid ${RCA.paper3}`, borderRadius: embedded ? 0 : 10, overflow: "hidden" }}>
      {children}
    </div>
  );

  const Header = () => (
    <div style={{ padding: "12px 18px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
      <div style={{ width: 28, height: 28, borderRadius: 7, background: RCA.ink, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        <I name="book" size={15} color={RCA.accent}/>
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: RCA.ink }}>Knowledge wiki</div>
        <div style={{ fontSize: 10.5, color: RCA.textPaperD, fontFamily: RCA.fMono, marginTop: 1 }}>
          {liveState === "building" ? "regenerating…" : "AI-maintained · updated " + lastUpdated}
        </div>
      </div>
      <RcaChip tone="default" icon={<I name="sparkle" size={11}/>}>AI-maintained</RcaChip>
      <div style={{ flex: 1 }}/>
      {liveState === "building"
        ? <RcaChip tone="accent" icon={<I name="refresh" size={11}/>}>Regenerating…</RcaChip>
        : <Btn size="sm" variant="secondary" icon={<I name="refresh" size={13}/>} onClick={regenerate}>Regenerate</Btn>}
    </div>
  );


  // ===== STATE: disabled =====
  if (liveState === "disabled") {
    return wrap(
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: 40, gap: 16 }}>
        <div style={{ width: 56, height: 56, borderRadius: 14, background: RCA.paper2, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <I name="book" size={26} color={RCA.textPaperD2}/>
        </div>
        <div style={{ maxWidth: 380 }}>
          <h2 className="display" style={{ fontSize: 22, marginBottom: 8 }}>The wiki is off for this collection</h2>
          <p style={{ fontSize: 14, color: RCA.textPaperD, lineHeight: 1.55, margin: 0 }}>
            Turn it on and the assistant will build a cross-linked summary of these documents — and keep it current as you upload more.
          </p>
        </div>
        <Btn variant="primary" icon={<I name="book" size={14}/>}>Turn on the wiki</Btn>
      </div>
    );
  }

  // ===== STATE: empty =====
  if (liveState === "empty") {
    return wrap(<>
      <Header/>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: 40, gap: 16, borderTop: `1px solid ${RCA.paper3}` }}>
        <div style={{ width: 56, height: 56, borderRadius: 14, background: RCA.accentSoft, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <I name="book" size={26} color={RCA.accentH}/>
        </div>
        <div style={{ maxWidth: 400 }}>
          <h2 className="display" style={{ fontSize: 22, marginBottom: 8 }}>The wiki hasn't been built yet</h2>
          <p style={{ fontSize: 14, color: RCA.textPaperD, lineHeight: 1.55, margin: 0 }}>
            This collection has the wiki turned on, but no pages have been written. Build it once and it will keep itself up to date as documents are added.
          </p>
        </div>
        <Btn variant="primary" icon={<I name="sparkle" size={14}/>} onClick={() => { setLiveState("building"); setTimeout(() => setLiveState("ready"), 2800); }}>Build the wiki</Btn>
        <div style={{ fontSize: 12, color: RCA.textPaperD2 }}>Takes a minute or two for {"~"}24 documents.</div>
      </div>
    </>);
  }

  // ===== STATE: building =====
  if (liveState === "building") {
    return wrap(<>
      <style>{`@keyframes wikiPulse { 0%,100% { opacity: .5; } 50% { opacity: 1; } }`}</style>
      <Header/>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: 40, gap: 18, borderTop: `1px solid ${RCA.paper3}` }}>
          <div style={{ width: 52, height: 52, borderRadius: 13, background: RCA.accentSoft, display: "flex", alignItems: "center", justifyContent: "center", animation: "wikiPulse 1.4s ease-in-out infinite" }}>
            <I name="refresh" size={24} color={RCA.accentH}/>
          </div>
          <div style={{ maxWidth: 400 }}>
            <h2 className="display" style={{ fontSize: 22, marginBottom: 8 }}>Updating the wiki…</h2>
            <p style={{ fontSize: 14, color: RCA.textPaperD, lineHeight: 1.55, margin: 0 }}>
              The assistant is reading the documents and writing pages. You can keep browsing pages that are already done.
            </p>
          </div>
          <div style={{ width: 280, display: "flex", flexDirection: "column", gap: 7 }}>
            {[["Reading documents", true], ["Identifying entities & concepts", true], ["Writing pages", false], ["Linking pages together", false]].map(([label, done], i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: done ? RCA.textPaper : RCA.textPaperD2 }}>
                {done
                  ? <span style={{ width: 16, height: 16, borderRadius: "50%", background: RCA.ok, display: "flex", alignItems: "center", justifyContent: "center" }}><I name="check" size={11} color={RCA.white}/></span>
                  : <span style={{ width: 16, height: 16, borderRadius: "50%", border: `2px solid ${RCA.paper3}` }}/>}
                <span style={{ textAlign: "left" }}>{label}</span>
                {i === 2 && <span className="mono" style={{ marginLeft: "auto", color: RCA.accent, fontSize: 11 }}>3 / 24</span>}
              </div>
            ))}
          </div>
      </div>
    </>);
  }

  // ===== STATE: ready =====
  return wrap(<>
    <Header/>
    <DocTreeView
      docs={wikiDocs}
      isAuto={isAuto}
      collection={{ id: "wiki-" + collectionName, title: collectionName }}
      onOpenSource={onOpenSource}
      storePrefix="rca-wiki-"
      directEdit
      sourcesByPath={sourcesByPath}
      onDropFiles={() => regenerate()}
      defaultPath="index.md"
      dropLabel="Drop docs to update the wiki"
      dropSub="the agent will regenerate it"
    />
  </>);
}

// ============================================================
// B. RETRIEVAL TOGGLES — for collection create / settings
// ============================================================
function RetrievalToggles({ docSearch = true, wiki = false, onChange }) {
  const [ds, setDs] = React.useState(docSearch);
  const [wk, setWk] = React.useState(wiki);
  const Toggle = ({ on, onClick }) => (
    <div onClick={onClick} style={{ width: 40, height: 22, borderRadius: 11, background: on ? RCA.accent : RCA.paper3, position: "relative", cursor: "pointer", flexShrink: 0, transition: "background .15s" }}>
      <div style={{ position: "absolute", top: 2, left: on ? 20 : 2, width: 18, height: 18, borderRadius: "50%", background: RCA.white, transition: "left .15s", boxShadow: "0 1px 2px rgba(0,0,0,.15)" }}/>
    </div>
  );
  const Row = ({ icon, title, desc, on, set, rec }) => (
    <div style={{ display: "flex", gap: 12, padding: 14, background: RCA.white, border: `1px solid ${on ? RCA.paper3 : RCA.paper3}`, borderRadius: 8, alignItems: "flex-start" }}>
      <div style={{ width: 32, height: 32, borderRadius: 7, background: on ? RCA.accentSoft : RCA.paper2, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        <I name={icon} size={16} color={on ? RCA.accentH : RCA.textPaperD}/>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: RCA.ink }}>{title}</span>
          {rec && <RcaChip tone="ok">Recommended</RcaChip>}
        </div>
        <div style={{ fontSize: 12.5, color: RCA.textPaperD, lineHeight: 1.5 }}>{desc}</div>
      </div>
      <Toggle on={on} onClick={() => set(!on)}/>
    </div>
  );
  return (
    <div className="rca" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Row icon="search" title="Document search" rec
        desc="Find passages from your documents to answer questions."
        on={ds} set={(v) => { setDs(v); onChange && onChange({ docSearch: v, wiki: wk }); }}/>
      <Row icon="book" title="Knowledge wiki"
        desc="An AI-built, cross-linked summary the assistant reads to answer. Updates as you upload."
        on={wk} set={(v) => { setWk(v); onChange && onChange({ docSearch: ds, wiki: v }); }}/>
      {ds && wk && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: RCA.accentSoft, borderRadius: 6 }}>
          <I name="sparkle" size={13} color={RCA.accentH}/>
          <span style={{ fontSize: 12, color: RCA.ink }}>Answers will draw on both — passages for detail, the wiki for the big picture.</span>
        </div>
      )}
    </div>
  );
}

// ============================================================
// C. "SEARCH THE WIKI" — one advanced row for the chat composer popover
// ============================================================
function WikiSearchRow({ checked = false, onChange }) {
  const [on, setOn] = React.useState(checked);
  return (
    <div className="rca" onClick={() => { setOn(!on); onChange && onChange(!on); }} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 12px", borderRadius: 8, cursor: "pointer", background: on ? RCA.accentSoft : "transparent" }}>
      <div style={{ width: 18, height: 18, borderRadius: 5, border: `1.5px solid ${on ? RCA.accent : RCA.paper3}`, background: on ? RCA.accent : RCA.white, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
        {on && <I name="check" size={12} color={RCA.white}/>}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <I name="book" size={13} color={on ? RCA.accentH : RCA.textPaperD}/>
          <span style={{ fontSize: 13, fontWeight: 500, color: RCA.ink }}>Search the wiki</span>
        </div>
        <div style={{ fontSize: 11.5, color: RCA.textPaperD, marginTop: 2 }}>Let the assistant read the collection's wiki for this question.</div>
      </div>
    </div>
  );
}

// ============================================================
// D. WIKI BADGE — for collection cards
// ============================================================
function WikiBadge({ size = "sm" }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: size === "sm" ? "2px 7px" : "3px 9px", borderRadius: 4, background: RCA.ink, color: RCA.paper, fontFamily: RCA.fMono, fontSize: size === "sm" ? 10 : 11, fontWeight: 500 }}>
      <I name="book" size={size === "sm" ? 10 : 12} color={RCA.accent}/> Wiki
    </span>
  );
}

Object.assign(window, { WikiBrowser, RetrievalToggles, WikiSearchRow, WikiBadge, WIKI_PAGES, WIKI_TREE });
