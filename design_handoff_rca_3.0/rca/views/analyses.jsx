// RCA analysis views — Pareto, Fishbone, 5-Why, Report draft
// Each is a self-contained component renderable as an investigation tab body.
// All assume RCA, I, RcaChip, Btn, Card, Avatar, CapsLabel are on window.

// ============================================================
// PARETO — top failure modes
// ============================================================
function ParetoView({ width = 940 }) {
  const data = [
    { mode: "void · BGA pad",      count: 142, pct: 0.41 },
    { mode: "void · QFN center",   count:  78, pct: 0.22 },
    { mode: "void · LGA edge",     count:  52, pct: 0.15 },
    { mode: "skew · CHIP-0402",    count:  31, pct: 0.09 },
    { mode: "tombstone · 0201",    count:  18, pct: 0.05 },
    { mode: "misalign · QFN",      count:  12, pct: 0.03 },
    { mode: "open · BGA corner",   count:   9, pct: 0.03 },
    { mode: "bridge · 0402",       count:   6, pct: 0.02 },
  ];
  const total = data.reduce((s, d) => s + d.count, 0);
  let cum = 0;
  const dataWithCum = data.map((d) => {
    cum += d.count;
    return { ...d, cumPct: cum / total };
  });
  const maxCount = Math.max(...data.map((d) => d.count));

  const W = width, H = 340;
  const PAD = { l: 56, r: 64, t: 18, b: 80 };
  const cw = (W - PAD.l - PAD.r) / data.length;
  const y = (v) => PAD.t + (1 - v / maxCount) * (H - PAD.t - PAD.b);
  const yLine = (p) => PAD.t + (1 - p) * (H - PAD.t - PAD.b);
  const linePath = dataWithCum.map((d, i) =>
    `${i ? "L" : "M"} ${PAD.l + i * cw + cw / 2} ${yLine(d.cumPct)}`).join(" ");

  return (
    <div style={{ padding: 22, background: RCA.white, borderRadius: 8, border: `1px solid ${RCA.paper3}` }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 18 }}>
        <div>
          <CapsLabel style={{ color: RCA.accent, marginBottom: 6 }}>03 · Pareto analysis</CapsLabel>
          <h3 className="display" style={{ fontSize: 22, marginBottom: 4 }}>Failure modes · 14d · MX-7 board</h3>
          <p style={{ fontSize: 13, color: RCA.textPaperD, margin: 0 }}>
            Three modes account for <strong style={{ color: RCA.ink }}>78%</strong> of all defects. The top mode — <strong style={{ color: RCA.accent }}>BGA pad voids</strong> — aligns with the reflow-zone-3 hypothesis.
          </p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Btn size="sm" iconRight={<I name="chev_d" size={11}/>}>14 days</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={11}/>}>by board</Btn>
          <Btn size="sm" variant="ghost" icon={<I name="download" size={13}/>}/>
        </div>
      </div>

      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        {/* grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((p, i) => (
          <line key={i} x1={PAD.l} x2={W - PAD.r} y1={yLine(p)} y2={yLine(p)} stroke={RCA.paper3} strokeDasharray="2 4"/>
        ))}
        {/* bars */}
        {dataWithCum.map((d, i) => {
          const x = PAD.l + i * cw + 6;
          const bw = cw - 12;
          const yy = y(d.count);
          const isTop3 = i < 3;
          return (
            <g key={i}>
              <rect x={x} y={yy} width={bw} height={H - PAD.b - yy} fill={isTop3 ? RCA.accent : RCA.ink2} opacity={isTop3 ? 1 : 0.85} rx="2"/>
              <text x={x + bw / 2} y={yy - 6} fontSize="11" fontFamily={RCA.fMono} fill={RCA.ink} textAnchor="middle">{d.count}</text>
              {/* x-label rotated */}
              <text x={x + bw / 2} y={H - PAD.b + 12} fontSize="11" fontFamily={RCA.fMono} fill={RCA.textPaperD} textAnchor="end" transform={`rotate(-32, ${x + bw / 2}, ${H - PAD.b + 12})`}>
                {d.mode}
              </text>
            </g>
          );
        })}
        {/* cumulative line */}
        <path d={linePath} fill="none" stroke={RCA.ink} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
        {dataWithCum.map((d, i) => (
          <g key={"p" + i}>
            <circle cx={PAD.l + i * cw + cw / 2} cy={yLine(d.cumPct)} r="3" fill={RCA.ink}/>
            {i < 4 && (
              <text x={PAD.l + i * cw + cw / 2} y={yLine(d.cumPct) - 8} fontSize="10" fontFamily={RCA.fMono} fill={RCA.textPaperD} textAnchor="middle">{(d.cumPct * 100).toFixed(0)}%</text>
            )}
          </g>
        ))}
        {/* 80% reference */}
        <line x1={PAD.l} x2={W - PAD.r} y1={yLine(0.8)} y2={yLine(0.8)} stroke={RCA.warn} strokeWidth="1" strokeDasharray="4 4"/>
        <text x={W - PAD.r - 4} y={yLine(0.8) - 4} fontSize="10" fontFamily={RCA.fMono} fill={RCA.warn} textAnchor="end">80% rule</text>
        {/* y axes */}
        <text x={PAD.l - 8} y={y(0) + 4} fontSize="10" fontFamily={RCA.fMono} fill={RCA.textPaperD} textAnchor="end">0</text>
        <text x={PAD.l - 8} y={y(maxCount)} fontSize="10" fontFamily={RCA.fMono} fill={RCA.textPaperD} textAnchor="end">{maxCount}</text>
        <text x={W - PAD.r + 6} y={yLine(0)} fontSize="10" fontFamily={RCA.fMono} fill={RCA.textPaperD}>0%</text>
        <text x={W - PAD.r + 6} y={yLine(1)} fontSize="10" fontFamily={RCA.fMono} fill={RCA.textPaperD}>100%</text>
      </svg>

      <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: RCA.textPaperD }}>
          <span style={{ width: 10, height: 10, background: RCA.accent, borderRadius: 2 }}/>Top contributors
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: RCA.textPaperD }}>
          <span style={{ width: 10, height: 10, background: RCA.ink2, borderRadius: 2 }}/>Long tail
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: RCA.textPaperD }}>
          <span style={{ width: 12, height: 2, background: RCA.ink }}/>Cumulative %
        </span>
      </div>
    </div>
  );
}

// ============================================================
// FISHBONE (Ishikawa) — 6M categories
// ============================================================
function FishboneView({ width = 940 }) {
  const W = width, H = 440;
  const spine = { x1: 80, y1: H / 2, x2: W - 100, y2: H / 2 };
  const categories = [
    { label: "Machine", side: "top", x: 220, items: [
      { t: "Reflow zone-3 PID drift", strong: true },
      { t: "Squeegee pressure variance" },
      { t: "Stencil wear (>2000 cycles)" },
    ]},
    { label: "Method", side: "top", x: 460, items: [
      { t: "Profile not retuned post-paste-change" },
      { t: "5-zone profile vs 7-zone spec" },
    ]},
    { label: "Material", side: "top", x: 720, items: [
      { t: "Paste open-life > 8h?" },
      { t: "Stencil aperture wear" },
    ]},
    { label: "Man",      side: "bot", x: 220, items: [
      { t: "Shift handover gap @ 14:00" },
      { t: "New operator on station 3" },
    ]},
    { label: "Measurement", side: "bot", x: 460, items: [
      { t: "AOI threshold calibration" },
      { t: "X-ray sample rate" },
    ]},
    { label: "Environment", side: "bot", x: 720, items: [
      { t: "Humidity rose 8%RH 08-14" },
      { t: "HVAC zone 2 setpoint" },
    ]},
  ];

  return (
    <div style={{ padding: 22, background: RCA.white, borderRadius: 8, border: `1px solid ${RCA.paper3}` }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <CapsLabel style={{ color: RCA.accent, marginBottom: 6 }}>04 · Fishbone (6M)</CapsLabel>
          <h3 className="display" style={{ fontSize: 22, marginBottom: 4 }}>Cause categories · Solder void spike</h3>
          <p style={{ fontSize: 13, color: RCA.textPaperD, margin: 0 }}>
            Orange branches = candidates supported by current evidence. Other branches kept open until ruled out.
          </p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Btn size="sm" variant="ghost" icon={<I name="plus" size={13}/>}>Add cause</Btn>
          <Btn size="sm" icon={<I name="sparkle" size={13}/>}>Agent suggest</Btn>
        </div>
      </div>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        {/* spine */}
        <line {...spine} stroke={RCA.ink2} strokeWidth="2.4" strokeLinecap="round"/>
        {/* arrowhead */}
        <path d={`M ${spine.x2} ${spine.y2} L ${spine.x2 + 18} ${spine.y2 - 9} L ${spine.x2 + 18} ${spine.y2 + 9} Z`} fill={RCA.ink2}/>
        {/* effect box */}
        <rect x={spine.x2 + 26} y={spine.y2 - 22} width={68} height={44} rx="6" fill={RCA.ink}/>
        <text x={spine.x2 + 60} y={spine.y2 - 4} textAnchor="middle" fontFamily={RCA.fSans} fontSize="12" fontWeight="700" fill={RCA.textDark}>Solder</text>
        <text x={spine.x2 + 60} y={spine.y2 + 12} textAnchor="middle" fontFamily={RCA.fSans} fontSize="12" fontWeight="700" fill={RCA.accent}>voids</text>

        {/* head label */}
        <text x={60} y={spine.y1 + 4} fontFamily={RCA.fMono} fontSize="10" fill={RCA.textPaperD} textAnchor="end">cause →</text>

        {categories.map((c, i) => {
          const isTop = c.side === "top";
          const startY = isTop ? spine.y1 - 8 : spine.y1 + 8;
          const labelY = isTop ? 38 : H - 18;
          const branchEndY = isTop ? 60 : H - 60;
          const dx = isTop ? -90 : 90; // angle direction
          return (
            <g key={i}>
              {/* main branch */}
              <line x1={c.x} y1={startY} x2={c.x + dx} y2={branchEndY} stroke={RCA.ink2} strokeWidth="1.8" strokeLinecap="round"/>
              {/* category label box */}
              <rect x={c.x + dx - 60} y={labelY - 16} width={120} height={22} rx="11" fill={RCA.paper2} stroke={RCA.paper3}/>
              <text x={c.x + dx} y={labelY - 1} textAnchor="middle" fontFamily={RCA.fSans} fontSize="12" fontWeight="600" fill={RCA.ink}>{c.label}</text>
              {/* sub-branches */}
              {c.items.map((it, j) => {
                const t = (j + 1) / (c.items.length + 1);
                const bx = c.x + dx * (1 - t * 0.7);
                const by = isTop ? spine.y1 - 8 - (spine.y1 - branchEndY) * t * 0.85 : spine.y1 + 8 + (branchEndY - spine.y1) * t * 0.85;
                const tipX = bx + (isTop ? -70 : 70);
                const tipY = by;
                return (
                  <g key={j}>
                    <line x1={bx} y1={by} x2={tipX} y2={tipY} stroke={it.strong ? RCA.accent : RCA.textPaperD2} strokeWidth={it.strong ? 1.8 : 1.2}/>
                    <text x={tipX + (isTop ? -6 : 6)} y={tipY + 4} fontSize="11" fontFamily={RCA.fBody} fill={it.strong ? RCA.accent : RCA.textPaper} textAnchor={isTop ? "end" : "start"} fontWeight={it.strong ? 600 : 400}>{it.t}</text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ============================================================
// 5-WHY chain
// ============================================================
function FiveWhyView({ width = 720 }) {
  const chain = [
    { q: "Why did solder void rate spike to 3.2%?", a: "Reflow zone-3 actual temperature dropped 3.2°C below set-point.", confidence: 0.92 },
    { q: "Why did zone-3 temperature drop?",         a: "Zone-3 PID controller failed to maintain set-point under increased throughput.", confidence: 0.84 },
    { q: "Why did the PID fail to maintain set-point?", a: "PID gains were tuned for 5-zone profile; throughput change made effective dwell different.", confidence: 0.71 },
    { q: "Why were the gains never retuned?",        a: "Throughput increase wasn't flagged as a process change requiring retune.", confidence: 0.66 },
    { q: "Why isn't throughput change flagged?",     a: "Change-control matrix lists material & speed-only — not throughput aggregation. ROOT.", confidence: 0.61, root: true },
  ];

  return (
    <div style={{ padding: 22, background: RCA.white, borderRadius: 8, border: `1px solid ${RCA.paper3}`, width }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 18 }}>
        <div>
          <CapsLabel style={{ color: RCA.accent, marginBottom: 6 }}>05 · 5-Why chain</CapsLabel>
          <h3 className="display" style={{ fontSize: 22, marginBottom: 4 }}>From spike to root</h3>
          <p style={{ fontSize: 13, color: RCA.textPaperD, margin: 0 }}>
            Agent-drafted; edit any answer to fork the chain. Confidence drops as we go deeper — that's expected.
          </p>
        </div>
        <Btn size="sm" icon={<I name="sparkle" size={13}/>}>Re-draft with agent</Btn>
      </div>

      <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 14 }}>
        {chain.map((step, i) => (
          <li key={i} style={{ position: "relative", paddingLeft: 56 }}>
            {/* connector */}
            {i < chain.length - 1 && (
              <span style={{ position: "absolute", left: 19, top: 40, bottom: -14, width: 2, background: step.root ? RCA.accent : RCA.paper3 }}/>
            )}
            {/* number badge */}
            <div style={{
              position: "absolute", left: 0, top: 0,
              width: 40, height: 40, borderRadius: "50%",
              background: step.root ? RCA.accent : RCA.ink, color: RCA.white,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontFamily: RCA.fSans, fontWeight: 700, fontSize: 15,
            }}>
              {step.root ? <I name="flame" size={18} color={RCA.white}/> : i + 1}
            </div>
            <div style={{ background: step.root ? RCA.accentSoft : RCA.paper, border: `1px solid ${step.root ? RCA.accent : RCA.paper3}`, borderRadius: 8, padding: "10px 14px" }}>
              <div style={{ fontSize: 12, color: RCA.textPaperD, marginBottom: 4 }}>{step.root ? "Root cause" : `Why #${i + 1}`}</div>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>{step.q}</div>
              <div style={{ fontSize: 13, color: step.root ? RCA.accentH : RCA.textPaper, lineHeight: 1.5 }}>
                {step.a}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
                <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>confidence</span>
                <div style={{ flex: 1, height: 4, background: RCA.paper3, borderRadius: 2, maxWidth: 200 }}>
                  <div style={{ width: `${step.confidence * 100}%`, height: "100%", background: step.root ? RCA.accent : RCA.ink, borderRadius: 2 }}/>
                </div>
                <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD }}>{(step.confidence * 100).toFixed(0)}%</span>
                <Btn size="sm" variant="ghost" icon={<I name="dots_h" size={12}/>}/>
              </div>
            </div>
          </li>
        ))}
      </ol>

      {/* Corrective actions */}
      <div style={{ marginTop: 24, paddingTop: 18, borderTop: `1px solid ${RCA.paper3}` }}>
        <CapsLabel style={{ marginBottom: 10 }}>Corrective actions · drafted</CapsLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {[
            { kind: "Containment", title: "Hold lot 25-W14 from MX-7 shipment", owner: "Alice", due: "today" },
            { kind: "Corrective", title: "Re-tune reflow zone-3 PID for current throughput", owner: "Bob", due: "08-17" },
            { kind: "Preventive", title: "Add throughput change to change-control matrix", owner: "Carol", due: "08-21" },
            { kind: "Preventive", title: "Add SPC alarm for zone-actual vs set-point > 2°C", owner: "Bob", due: "08-24" },
          ].map((a, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 12px", border: `1px solid ${RCA.paper3}`, borderRadius: 6, background: RCA.paper }}>
              <RcaChip tone={a.kind === "Containment" ? "err" : a.kind === "Corrective" ? "warn" : "ok"}>{a.kind}</RcaChip>
              <span style={{ flex: 1, fontSize: 13 }}>{a.title}</span>
              <Avatar name={a.owner.slice(0, 2)} size={22}/>
              <span style={{ fontSize: 12, color: RCA.textPaperD, fontFamily: RCA.fMono, minWidth: 56, textAlign: "right" }}>{a.due}</span>
              <Btn size="sm" variant="ghost" icon={<I name="dots_h" size={12}/>}/>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// REPORT — final report with version history (multiple versions, latest supersedes)
// ============================================================
const REPORT_VERSIONS = [
  {
    v: 3, current: true, ts: "08-16 17:42", author: "agent + Alice",
    summary: "Re-tuned containment scope; expanded preventive to cover throughput-aggregation flags.",
    sections: [
      { label: "D1 · Team",        body: "Alice Chen (process), Bob Liu (reflow), Carol Kao (quality), Dan Jen (yield)." },
      { label: "D2 · Problem",     body: "Void rate on MX-7 board climbed from 1.4% baseline to 3.2% starting 08-14 14:00, sustained across 4 shifts. Affects lot 25-W14, Line 3.", emphasize: true },
      { label: "D3 · Containment", body: "Lot 25-W14 held at outgoing. Lots 25-W12, 25-W13 sampled for void rate. AOI sampling raised from 1/100 to 1/10 on Line 3. 4-hour incident review cadence active." },
      { label: "D4 · Root cause",
        body: "Reflow zone-3 PID gains, tuned for the prior throughput profile, failed to maintain 245°C set-point under the current throughput. The throughput increase (effective 08-12) was not flagged by change-control as requiring PID retune. Drift of ~3.2°C preceded the void spike by 30 minutes and persisted.",
        emphasize: true },
      { label: "D5 · Corrective",  body: "Re-tune zone-3 PID against current throughput. Verify across 2 shifts at AOI sampling 1/10. Resume normal sampling once void rate < 1.6% for 2 consecutive shifts." },
      { label: "D6 · Verification",body: "AOI void rate over 48h post-correction. Reflow zone-3 actual vs set-point error < 1°C 95th percentile." },
      { label: "D7 · Preventive",  body: "Update change-control matrix to flag throughput aggregations > 8%. Add SPC alarm for zone-actual vs set-point delta > 2°C across 15 min window. Train shift leads on change-control checklist." },
      { label: "D8 · Close-out",   body: "Pending verification window completion (est. 08-18). Reviewers: Quality lead, Manufacturing manager." },
    ],
  },
  { v: 2, current: false, ts: "08-16 16:08", author: "agent", summary: "First full draft with preventive actions; missed throughput-aggregation framing." },
  { v: 1, current: false, ts: "08-15 14:48", author: "agent", summary: "Initial draft — hypothesized paste-age only; no PID drift evidence yet." },
];

function ReportView({ width = 760 }) {
  const [selectedV, setSelectedV] = React.useState(3);
  const v = REPORT_VERSIONS.find((r) => r.v === selectedV) || REPORT_VERSIONS[0];
  const isCurrent = v.current;

  return (
    <div style={{ width }}>
      {/* Version selector strip */}
      <div style={{ background: RCA.ink, color: RCA.textDark, borderRadius: 8, padding: "14px 18px", marginBottom: 18, display: "flex", alignItems: "center", gap: 16 }}>
        <I name="file" size={18} color={RCA.accent}/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Final report</div>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            {REPORT_VERSIONS.map((rv) => (
              <div key={rv.v} onClick={() => setSelectedV(rv.v)} style={{
                padding: "3px 9px", borderRadius: 4, cursor: "pointer",
                background: selectedV === rv.v ? RCA.accent : "transparent",
                border: `1px solid ${selectedV === rv.v ? RCA.accent : RCA.ink4}`,
                color: selectedV === rv.v ? RCA.white : RCA.textDarkD,
                fontFamily: RCA.fMono, fontSize: 11, fontWeight: 500,
                display: "inline-flex", alignItems: "center", gap: 5,
              }}>
                v{rv.v}
                {rv.current && <span style={{ fontSize: 9, opacity: 0.85 }}>· current</span>}
                {!rv.current && <span style={{ fontSize: 9, opacity: 0.65 }}>· superseded</span>}
              </div>
            ))}
            <span style={{ fontFamily: RCA.fMono, fontSize: 11, color: RCA.textDarkD2, marginLeft: 6 }}>{v.ts} · by {v.author}</span>
          </div>
        </div>
        <Btn size="sm" variant="ghost" onDark icon={<I name="download" size={13}/>}>Export PDF</Btn>
        <Btn size="sm" variant="primary" icon={<I name="sparkle" size={13}/>}>Generate new version</Btn>
      </div>

      {/* Superseded-version notice */}
      {!isCurrent && (
        <div style={{ padding: "10px 14px", background: RCA.paper2, border: `1px solid ${RCA.paper3}`, borderLeft: `3px solid ${RCA.textPaperD2}`, borderRadius: 6, marginBottom: 18, display: "flex", alignItems: "center", gap: 12 }}>
          <I name="clock" size={14} color={RCA.textPaperD}/>
          <span style={{ flex: 1, fontSize: 13, color: RCA.textPaperD }}>
            Viewing <strong style={{ color: RCA.ink }}>v{v.v}</strong> — superseded by v3. Read-only.
          </span>
          <Btn size="sm" variant="ghost" iconRight={<I name="arrow_r" size={12}/>} onClick={() => setSelectedV(3)}>Go to current</Btn>
        </div>
      )}

      {/* Report body */}
      <div style={{ padding: "32px 40px", background: RCA.white, borderRadius: 8, border: `1px solid ${RCA.paper3}`, opacity: isCurrent ? 1 : 0.85, position: "relative" }}>
        {!isCurrent && (
          <div style={{
            position: "absolute", top: 22, right: 30,
            fontFamily: RCA.fMono, fontSize: 13, fontWeight: 700,
            color: RCA.textPaperD2, letterSpacing: "0.18em",
            border: `2px solid ${RCA.textPaperD2}`,
            padding: "4px 10px", borderRadius: 4,
            transform: "rotate(-6deg)",
            textTransform: "uppercase",
          }}>Superseded</div>
        )}
        {/* report header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24, paddingBottom: 18, borderBottom: `1px solid ${RCA.paper3}` }}>
          <div>
            <CapsLabel style={{ color: RCA.accent, marginBottom: 8 }}>RCA report · v{v.v}</CapsLabel>
            <h2 className="display" style={{ fontSize: 28, marginBottom: 6 }}>Solder voids spike <span style={{ color: RCA.accent }}>·</span> Line 3</h2>
            <div style={{ display: "flex", gap: 10, alignItems: "center", color: RCA.textPaperD, fontSize: 13 }}>
              <span>Owner: Alice Chen</span><span>·</span>
              <span>Severity: P1</span><span>·</span>
              <span>Generated: {v.ts}</span><span>·</span>
              {isCurrent ? <RcaChip dot tone="accent">current · awaiting review</RcaChip> : <RcaChip tone="default">superseded</RcaChip>}
            </div>
          </div>
          {isCurrent && (
            <Btn size="sm" variant="primary" icon={<I name="check" size={13}/>}>Submit for review</Btn>
          )}
        </div>

        {/* version change note */}
        {v.summary && (
          <div style={{ marginBottom: 22, padding: "10px 14px", background: RCA.accentSoft, borderLeft: `3px solid ${RCA.accent}`, borderRadius: 4, fontSize: 13, color: RCA.ink, lineHeight: 1.5 }}>
            <strong>What changed in v{v.v}: </strong>{v.summary}
          </div>
        )}

        {(v.sections || REPORT_VERSIONS[0].sections).map((s, i) => (
          <div key={i} style={{ marginBottom: 18 }}>
            <div className="caps" style={{ fontSize: 11, color: RCA.accent, marginBottom: 6 }}>{s.label}</div>
            <p style={{
              fontSize: 14, lineHeight: 1.6, margin: 0,
              color: s.emphasize ? RCA.ink : RCA.textPaper,
              background: s.emphasize ? RCA.accentSoft : "transparent",
              padding: s.emphasize ? "10px 14px" : 0,
              borderLeft: s.emphasize ? `3px solid ${RCA.accent}` : "none",
              borderRadius: s.emphasize ? 4 : 0,
            }}>{s.body}</p>
          </div>
        ))}

        <div style={{ marginTop: 24, paddingTop: 18, borderTop: `1px solid ${RCA.paper3}`, display: "flex", justifyContent: "space-between", color: RCA.textPaperD, fontSize: 12, fontFamily: RCA.fMono }}>
          <span>generated by RCA 3.0 · {v.author}</span>
          <span>v{v.v} · {v.ts}</span>
        </div>
      </div>

      {/* Version history */}
      <div style={{ marginTop: 22 }}>
        <CapsLabel style={{ marginBottom: 10 }}>Version history</CapsLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {REPORT_VERSIONS.map((rv) => (
            <div key={rv.v} onClick={() => setSelectedV(rv.v)} style={{
              display: "flex", alignItems: "center", gap: 14,
              padding: "10px 14px",
              background: selectedV === rv.v ? RCA.white : "transparent",
              border: `1px solid ${selectedV === rv.v ? RCA.accent : RCA.paper3}`,
              borderRadius: 6,
              cursor: "pointer",
            }}>
              <span className="mono" style={{ fontSize: 13, fontWeight: 700, width: 28, color: rv.current ? RCA.accent : RCA.textPaperD }}>v{rv.v}</span>
              {rv.current
                ? <RcaChip dot tone="accent">current</RcaChip>
                : <RcaChip tone="default">superseded</RcaChip>
              }
              <span style={{ fontSize: 13, color: RCA.textPaper, flex: 1, lineHeight: 1.4 }}>{rv.summary}</span>
              <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>{rv.ts} · {rv.author}</span>
              <I name="chev_r" size={13} color={RCA.textPaperD2}/>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// NEW INVESTIGATION modal
// ============================================================
function NewInvestigation({ onClose, onCreate }) {
  return (
    <div style={{
      position: "absolute", inset: 0,
      background: "rgba(20,22,28,0.55)",
      backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 50,
    }}>
      <div style={{ width: 620, maxHeight: "90%", background: RCA.paper, borderRadius: 12, border: `1px solid ${RCA.paper3}`, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "18px 22px 14px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <CapsLabel style={{ marginBottom: 6 }}>New investigation</CapsLabel>
            <h2 className="display" style={{ fontSize: 22 }}>Start an RCA</h2>
          </div>
          <Btn size="sm" variant="ghost" icon={<I name="x" size={14}/>} onClick={onClose}/>
        </div>
        <div style={{ padding: "20px 22px", display: "flex", flexDirection: "column", gap: 14, overflow: "auto" }}>
          <Field label="Title" required>
            <div style={{ display: "flex", alignItems: "center", height: 38, padding: "0 12px", background: RCA.white, border: `1.5px solid ${RCA.accent}`, borderRadius: 6 }}>
              <span style={{ fontSize: 14 }}>Solder voids spike — Line 3 — MX-7</span>
              <span style={{ width: 1.5, height: 16, background: RCA.accent, marginLeft: 2 }}/>
            </div>
          </Field>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <Field label="Severity">
              <Picker options={["P0 · halt", "P1 · critical", "P2 · major", "P3 · minor", "P4 · cosmetic"]} active={1}/>
            </Field>
            <Field label="Status">
              <Picker options={["draft", "triaging"]} active={1}/>
            </Field>
            <Field label="Production line">
              <Select value="Line 3 · Reflow"/>
            </Field>
            <Field label="Product / part">
              <Select value="MX-7 board"/>
            </Field>
            <Field label="Lot · batch">
              <Select value="25-W14" mono/>
            </Field>
            <Field label="Owner">
              <div style={{ display: "flex", alignItems: "center", gap: 8, height: 38, padding: "0 10px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                <Avatar name="AC" size={22}/>
                <span style={{ fontSize: 13 }}>Alice Chen</span>
              </div>
            </Field>
          </div>

          <Field label="Template">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
              {[
                { name: "Solder defect", desc: "SPC + AOI + 5-why + 8D", active: true },
                { name: "Yield drop", desc: "Yield trace + Pareto" },
                { name: "Blank", desc: "Empty notebook" },
              ].map((t, i) => (
                <div key={i} style={{ padding: 12, background: t.active ? RCA.accentSoft : RCA.white, border: `1.5px solid ${t.active ? RCA.accent : RCA.paper3}`, borderRadius: 6, cursor: "pointer" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2, color: t.active ? RCA.accentH : RCA.ink }}>{t.name}</div>
                  <div style={{ fontSize: 11, color: RCA.textPaperD }}>{t.desc}</div>
                </div>
              ))}
            </div>
          </Field>

          <Field label="Initial brief / what's happening">
            <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6, padding: 12, minHeight: 90, fontSize: 13, lineHeight: 1.5, color: RCA.textPaper }}>
              Void rate climbed from 1.4% baseline to 3.2% starting 08-14 14:00 on Line 3. AOI sampling confirms. No process changes logged. Need root cause + containment.
            </div>
          </Field>

          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 12, background: RCA.accentSoft, border: `1px solid ${RCA.accent}33`, borderRadius: 6 }}>
            <I name="sparkle" size={16} color={RCA.accent}/>
            <span style={{ fontSize: 13, color: RCA.ink, flex: 1 }}>Agent will start the first 3 plan steps automatically once created.</span>
            <input type="checkbox" checked readOnly style={{ accentColor: RCA.accent }}/>
          </div>
        </div>
        <div style={{ padding: "14px 22px", borderTop: `1px solid ${RCA.paper3}`, display: "flex", justifyContent: "flex-end", gap: 8, background: RCA.paper2 }}>
          <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
          <Btn variant="primary" icon={<I name="sparkle" size={14}/>} onClick={onCreate}>Create & ask agent</Btn>
        </div>
      </div>
    </div>
  );
}

function Field({ label, required, children }) {
  return (
    <div>
      <div className="caps" style={{ marginBottom: 6, color: RCA.textPaperD }}>{label}{required && <span style={{ color: RCA.accent, marginLeft: 4 }}>*</span>}</div>
      {children}
    </div>
  );
}
function Picker({ options, active }) {
  return (
    <div style={{ display: "inline-flex", border: `1px solid ${RCA.paper3}`, borderRadius: 6, padding: 3, background: RCA.white, flexWrap: "wrap", gap: 3 }}>
      {options.map((o, i) => (
        <div key={i} style={{ padding: "4px 10px", borderRadius: 4, fontSize: 12, background: i === active ? RCA.ink : "transparent", color: i === active ? RCA.textDark : RCA.textPaper, fontFamily: RCA.fMono, cursor: "pointer" }}>{o}</div>
      ))}
    </div>
  );
}
function Select({ value, mono }) {
  return (
    <div style={{ display: "flex", alignItems: "center", height: 38, padding: "0 12px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
      <span style={{ fontSize: 13, flex: 1, fontFamily: mono ? RCA.fMono : RCA.fBody }}>{value}</span>
      <I name="chev_d" size={13} color={RCA.textPaperD}/>
    </div>
  );
}

Object.assign(window, { ParetoView, FishboneView, FiveWhyView, ReportView, NewInvestigation });
