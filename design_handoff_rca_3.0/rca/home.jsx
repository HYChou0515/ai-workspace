// RCA Home — investigation list (hi-fi)
// Uses RCA, Btn, RcaChip, Card, I, Sparkline, Hatch2, Avatar, StatDot from rca/system.jsx

const HOME_W = 1440;
const HOME_H = 900;

const INVESTIGATIONS = [
{
  id: "INC-2026-0142",
  title: "Solder voids spike",
  summary: "Void rate 2.3× baseline since 08-14 14:00",
  severity: "P1", sevTone: "err",
  topics: ["Reflow zone-3"], product: "MX-7 board", lot: "25-W14",
  status: "triaging", statusTone: "warn",
  owner: { name: "Alice Chen", initials: "AC" },
  members: ["AC", "BL", "DJ", "EH"],
  updated: "12 min ago",
  agent: "running",
  pinned: true
},
{
  id: "INC-2026-0141",
  title: "Dead pixel cluster, top-left corner",
  summary: "12 panels at incoming inspection, cluster within 80×80 region",
  severity: "P2", sevTone: "warn",
  topics: ["Panel inspection", "Display M7-S"], product: "Display M7-S", lot: "25-W13",
  status: "awaiting review", statusTone: "accent",
  owner: { name: "Bob Liu", initials: "BL" },
  members: ["BL", "CK"],
  updated: "1 h ago",
  agent: "idle",
  reportProgress: { drafted: 6, total: 8 },
  pinned: true
},
{
  id: "INC-2026-0140",
  title: "Crack at flange · gen-2 housing",
  summary: "5 of 240 units cracked at injection-point flange",
  severity: "P2", sevTone: "warn",
  topics: ["Injection molding"], product: "Housing G2", lot: "25-W14",
  status: "triaging", statusTone: "warn",
  owner: { name: "Carol K.", initials: "CK" },
  members: ["CK", "DJ", "FH"],
  updated: "3 h ago",
  agent: "idle"
},
{
  id: "INC-2026-0139",
  title: "Yield drop -4.2pp at Cell test fixture #7",
  summary: "Drop persists 3 shifts; fixture contact resistance suspect",
  severity: "P1", sevTone: "err",
  topics: ["Cell test fixture", "Contact resistance", "Yield drop"], product: "Battery 18650", lot: "25-W14",
  status: "draft", statusTone: "default",
  owner: { name: "Dan J.", initials: "DJ" },
  members: ["DJ"],
  updated: "yesterday",
  agent: "idle"
},
{
  id: "INC-2026-0138",
  title: "Glue underfill voids — corner pads",
  summary: "Corner pads U7/U8 showing voids under X-ray inspection",
  severity: "P3", sevTone: "warn",
  topics: ["Underfill"], product: "Module N4", lot: "25-W12",
  status: "resolved", statusTone: "ok",
  owner: { name: "Eve H.", initials: "EH" },
  members: ["EH", "FH", "GR"],
  updated: "2 days ago",
  agent: "idle",
  reportV: "v2"
},
{
  id: "INC-2026-0137",
  title: "Wirebond pull strength under spec",
  summary: "Pull test results 3.8gf vs 4.5gf spec on lot 25-W12",
  severity: "P2", sevTone: "warn",
  topics: ["Wirebond"], product: "Sensor V2", lot: "25-W12",
  status: "resolved", statusTone: "ok",
  owner: { name: "Frank H.", initials: "FH" },
  members: ["FH", "GR"],
  updated: "3 days ago",
  agent: "idle",
  reportV: "v4"
},
{
  id: "INC-2026-0136",
  title: "Color delta on top-cover paint",
  summary: "ΔE > 2.0 on batch B-25-0814, supplier-side suspected",
  severity: "P3", sevTone: "warn",
  topics: ["Paint color"], product: "Top cover", lot: "25-W12",
  status: "resolved", statusTone: "ok",
  owner: { name: "Gail R.", initials: "GR" },
  members: ["GR"],
  updated: "5 days ago",
  agent: "idle",
  reportV: "v1"
},
{
  id: "INC-2026-0135",
  title: "Capacitor C14 placement offset",
  summary: "Investigation closed without root cause; defects stopped after vendor lot rotated",
  severity: "P4", sevTone: "default",
  topics: ["SMT placement"], product: "MX-7 board", lot: "25-W12",
  status: "abandoned", statusTone: "default",
  owner: { name: "Hank P.", initials: "HP" },
  members: ["HP"],
  updated: "1 wk ago",
  agent: "idle"
}];


function HomeRCA({ onSelect, onNew, onAskAgent, onOpenKB, onOpenChats }) {
  return (
    <div className="rca" style={{ width: HOME_W, height: HOME_H, background: RCA.paper, display: "flex", overflow: "hidden" }}>

      {/* SIDEBAR */}
      <aside style={{ width: 240, borderRight: `1px solid ${RCA.paper3}`, display: "flex", padding: "18px 0", flexDirection: "column" }}>
        <div style={{ padding: "20px 18px 16px", borderBottom: `1px solid ${RCA.paper3}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <RCAMark size={40} />
            <div style={{ display: "flex", flexDirection: "column", lineHeight: 1, gap: 6 }}>
              <div style={{ fontFamily: RCA.fSans, fontWeight: 800, fontSize: 24, letterSpacing: "-0.03em", display: "flex", alignItems: "center" }}>
                <span>RCA</span>
                <span style={{ width: 5, height: 5, background: RCA.accent, margin: "0 4px 0 6px", display: "inline-block" }} />
                <span>3.0</span>
              </div>
              <div style={{ fontFamily: RCA.fMono, fontSize: 8.5, fontWeight: 500, color: RCA.textPaperD, letterSpacing: "0.08em", whiteSpace: "nowrap", textTransform: "uppercase" }}>
                Analysis <span style={{ color: RCA.accent }}>.</span> AI <span style={{ color: RCA.accent }}>.</span> Agent
              </div>
            </div>
          </div>
          <Btn variant="primary" size="md" icon={<I name="plus" size={14} />} style={{ marginTop: 16 }} fullWidth onClick={onNew}>
            New investigation
          </Btn>
        </div>

        <nav style={{ padding: "8px 8px", display: "flex", flexDirection: "column", gap: 1 }}>
          {[
          ["bug", "All open", 14, true],
          ["pin", "Pinned", 2],
          ["user", "Owned by me", 3],
          ["users", "Watching", 6],
          ["clock", "Recently viewed"],
          ["check", "Resolved (30d)", 47],
          ["x", "Abandoned (30d)", 3],
          ["star", "Templates"]].
          map(([icn, label, badge, active], i) =>
          <NavItem key={i} icon={icn} label={label} badge={badge} active={active} />
          )}
        </nav>

        <div style={{ padding: "16px 8px 8px" }}>
          <CapsLabel style={{ marginBottom: 8, paddingLeft: 10 }}>Knowledge</CapsLabel>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <NavItem icon="layers" label="Knowledge base" badge={9} onClick={onOpenKB} />
            <NavItem icon="chat" label="Chats" badge={10} onClick={onOpenChats} />
          </div>
        </div>

        <div style={{ padding: "20px 18px 8px" }}>
          <CapsLabel style={{ marginBottom: 8 }}>Topics</CapsLabel>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {[
            ["SMT 1", 2], ["SMT 2", 0], ["Reflow", 1], ["Molding A", 1], ["Panel test 2", 1],
            ["Cell test", 1], ["Bonding 2", 0], ["Paint 1", 0]].
            map(([n, c], i) =>
            <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 10px", borderRadius: 4, fontSize: 13, color: RCA.textPaper }}>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <StatDot tone={c > 0 ? "accent" : "mute"} size={6} />{n}
                </span>
                <span className="mono" style={{ fontSize: 11, color: c > 0 ? RCA.accent : RCA.textPaperD2 }}>{c}</span>
              </div>
            )}
          </div>
        </div>

        <div style={{ marginTop: "auto", padding: "12px 14px", borderTop: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "center", gap: 10 }}>
          <Avatar name="AC" size={28} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Alice Chen</div>
            <div style={{ fontSize: 11, color: RCA.textPaperD }}>SMT process · admin</div>
          </div>
          <I name="settings" size={14} color={RCA.textPaperD} />
        </div>
      </aside>

      {/* MAIN */}
      <main style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* top bar */}
        <div style={{ height: 64, padding: "0 28px", display: "flex", alignItems: "center", borderBottom: `1px solid ${RCA.paper3}`, gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 12px", height: 38, width: 420, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={15} color={RCA.textPaperD} />
            <span style={{ color: RCA.textPaperD, fontSize: 13, flex: 1 }}>Search investigations, defects, parts, logs…</span>
            <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD, padding: "2px 6px", border: `1px solid ${RCA.paper3}`, borderRadius: 4 }}>⌘K</span>
          </div>
          <div style={{ flex: 1 }} />
          <Btn variant="ghost" icon={<I name="bell" size={15} />}>3</Btn>
          <Btn icon={<I name="sparkle" size={14} />} onClick={onAskAgent}>Ask agent</Btn>
        </div>

        {/* page header */}
        <div style={{ padding: "28px 28px 20px", display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, fontFamily: "sans-serif" }}>
          <div>
            <CapsLabel style={{ marginBottom: 10 }}>Investigations</CapsLabel>
            <h1 className="display" style={{ fontSize: 40, fontFamily: "system-ui" }}>
              14 open <span style={{ color: RCA.accent }}>·</span> 4 critical
            </h1>
            <p style={{ color: RCA.textPaperD, fontSize: 14, marginTop: 8 }}>All investigations are visible to the org. Pin the ones you own.</p>
          </div>
          <div style={{ display: "flex", gap: 24 }}>
            <Metric label="Resolution time (P1)" value="3.2 d" trend={-12} sub="last 30d" />
            <Metric label="Open · P1" value="4" trend={1} sub="vs last wk" inverse />
            <Metric label="Agent runs · today" value="38" trend={null} sub="across 12 invs" />
          </div>
        </div>

        {/* tabs + filters */}
        <div style={{ padding: "0 28px", borderBottom: `1px solid ${RCA.paper3}`, display: "flex", alignItems: "stretch", gap: 28 }}>
          {[
          ["All", 14, true],
          ["My open", 3],
          ["Watching", 6],
          ["Triaging", 7],
          ["Awaiting review", 2],
          ["Resolved", 47],
          ["Abandoned", 3]].
          map(([t, c, act], i) =>
          <div key={i} style={{ padding: "12px 0", borderBottom: act ? `2px solid ${RCA.accent}` : `2px solid transparent`, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <span style={{ fontSize: 14, fontWeight: act ? 600 : 400, color: act ? RCA.ink : RCA.textPaperD }}>{t}</span>
              <span className="mono" style={{ fontSize: 11, color: act ? RCA.accent : RCA.textPaperD2 }}>{c}</span>
            </div>
          )}
        </div>

        {/* filter strip */}
        <div style={{ padding: "16px 28px", display: "flex", gap: 8, alignItems: "center" }}>
          <Btn size="sm" icon={<I name="filter" size={13} />}>Filter</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12} />}>Severity · any</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12} />}>Topic · any</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12} />}>Owner · any</Btn>
          <Btn size="sm" iconRight={<I name="chev_d" size={12} />}>Updated · any</Btn>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, color: RCA.textPaperD }}>Sort: <strong style={{ color: RCA.ink }}>Updated</strong></span>
          <span style={{ fontSize: 12, color: RCA.textPaperD }}>· showing {INVESTIGATIONS.length} of 142</span>
          <div style={{ width: 1, height: 18, background: RCA.paper3, margin: "0 8px" }} />
          <Btn size="sm" variant="ghost" icon={<I name="table" size={13} />} />
          <Btn size="sm" variant="ghost" icon={<I name="grid" size={13} />} />
        </div>

        {/* TABLE */}
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "0 28px 28px" }}>
          <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, overflow: "hidden" }}>
            {/* head */}
            <div style={{ display: "grid", gridTemplateColumns: "32px 2.6fr 0.9fr 1.3fr 1fr 1fr 1fr 32px", padding: "10px 16px", borderBottom: `1px solid ${RCA.paper3}`, alignItems: "center", gap: 10 }}>
              <div></div>
              {["Investigation", "Severity", "Topic · product", "Owner", "Updated", "Agent"].map((h, i) =>
              <div key={i} className="caps" style={{ fontSize: 10, color: RCA.textPaperD }}>{h}</div>
              )}
              <div></div>
            </div>
            {/* rows */}
            {INVESTIGATIONS.map((r, i) =>
            <div key={r.id} onClick={() => onSelect && onSelect(r.id)} style={{ display: "grid", gridTemplateColumns: "32px 2.6fr 0.9fr 1.3fr 1fr 1fr 1fr 32px", padding: "14px 16px", alignItems: "center", gap: 10, borderBottom: i < INVESTIGATIONS.length - 1 ? `1px solid ${RCA.paper3}` : "none", background: i === 0 ? RCA.accentSoft + "55" : "transparent", cursor: "pointer" }}>
                <div style={{ color: r.pinned ? RCA.accent : RCA.textPaperD2 }}>
                  <I name="pin" size={14} />
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: RCA.ink, marginBottom: 2 }}>{r.title}</div>
                  <div style={{ fontSize: 12, color: RCA.textPaperD }}>{r.summary}</div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "nowrap", overflow: "hidden" }}>
                    <RcaChip dot tone={r.statusTone}>{r.status}</RcaChip>
                    {r.agent === "running" && <RcaChip tone="accentSolid" icon={<I name="sparkle" size={10} />}>agent</RcaChip>}
                    {(r.reportProgress || r.reportV) &&
                  <RcaChip tone="accent" icon={<I name="file" size={10} />}>
                        report · {r.reportV || "v3"}
                      </RcaChip>
                  }
                  </div>
                </div>
                <div>
                  <RcaChip dot tone={r.sevTone}>{r.severity}</RcaChip>
                </div>
                <div style={{ fontSize: 13, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", overflow: "hidden" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{r.topics[0]}</span>
                    {r.topics.length > 1 &&
                  <span title={r.topics.slice(1).join(", ")} style={{ flexShrink: 0, padding: "1px 5px", border: `1px solid ${RCA.paper3}`, borderRadius: 3, fontSize: 10, color: RCA.textPaperD, fontFamily: RCA.fMono, background: RCA.paper }}>
                        +{r.topics.length - 1}
                      </span>
                  }
                  </div>
                  <div style={{ fontSize: 11, color: RCA.textPaperD, fontFamily: RCA.fMono, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.product}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Avatar name={r.owner.initials} size={26} />
                  <div style={{ fontSize: 13 }}>{r.owner.name}</div>
                </div>
                <div style={{ fontSize: 13, color: RCA.textPaperD }}>{r.updated}</div>
                <div>
                  <Sparkline w={80} h={22} />
                </div>
                <div title="Row actions" style={{ color: RCA.textPaperD2, display: "flex", justifyContent: "center", alignItems: "center" }}>
                  <I name="dots_v" size={16} />
                </div>
              </div>
            )}
            {/* footer */}
            <div style={{ padding: "10px 16px", display: "flex", justifyContent: "space-between", alignItems: "center", background: RCA.paper2, borderTop: `1px solid ${RCA.paper3}` }}>
              <span style={{ fontSize: 12, color: RCA.textPaperD }}>1–{INVESTIGATIONS.length} of 142</span>
              <div style={{ display: "flex", gap: 4 }}>
                <Btn size="sm" variant="ghost" icon={<I name="chev_l" size={12} />} />
                <span className="mono" style={{ fontSize: 12, color: RCA.textPaperD, padding: "6px 8px" }}>1 / 12</span>
                <Btn size="sm" variant="ghost" icon={<I name="chev_r" size={12} />} />
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>);

}

function NavItem({ icon, label, badge, active, onClick }) {
  return (
    <div onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "7px 10px",
      borderRadius: 4,
      background: active ? RCA.accentSoft : "transparent",
      color: active ? RCA.accentH : RCA.textPaper,
      cursor: "pointer"
    }}>
      <I name={icon} size={15} color={active ? RCA.accentH : RCA.textPaperD} />
      <span style={{ fontSize: 13, fontWeight: active ? 600 : 400, flex: 1 }}>{label}</span>
      {badge != null &&
      <span className="mono" style={{ fontSize: 11, color: active ? RCA.accent : RCA.textPaperD2, padding: "1px 6px", background: active ? RCA.white : "transparent", borderRadius: 4 }}>{badge}</span>
      }
    </div>);

}

function Metric({ label, value, sub, trend, inverse }) {
  const dir = trend == null ? null : trend > 0 ? "up" : "down";
  const good = inverse ? dir === "down" : dir === "up";
  return (
    <div style={{ minWidth: 140 }}>
      <CapsLabel style={{ marginBottom: 6 }}>{label}</CapsLabel>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <div className="display" style={{ fontSize: 30 }}>{value}</div>
        {dir &&
        <span className="mono" style={{ fontSize: 11, color: good ? RCA.ok : RCA.err }}>
            {dir === "up" ? "↑" : "↓"}{Math.abs(trend)}
          </span>
        }
      </div>
      <div style={{ fontSize: 11, color: RCA.textPaperD2, marginTop: 2 }}>{sub}</div>
    </div>);

}

Object.assign(window, { HomeRCA, HOME_W, HOME_H, INVESTIGATIONS });