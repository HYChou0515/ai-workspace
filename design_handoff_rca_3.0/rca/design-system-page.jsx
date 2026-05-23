// RCA Design System — showcase page
// Loads after rca/system.jsx (which exposes RCA, Btn, Card, I, etc. on window)

const DS_W = 1280;

function DSScreen() {
  return (
    <div className="rca" style={{ width: DS_W, background: RCA.paper, padding: "56px 64px", display: "flex", flexDirection: "column", gap: 56 }}>

      {/* HERO */}
      <header style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, borderBottom: `1px solid ${RCA.paper3}`, paddingBottom: 40 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <RCALockup size={36}/>
          <h1 className="display" style={{ fontSize: 56, maxWidth: 720, letterSpacing: "-0.035em" }}>
            Design system <span style={{ color: RCA.accent }}>.</span> v1
          </h1>
          <p style={{ fontSize: 16, color: RCA.textPaperD, maxWidth: 560, lineHeight: 1.5, margin: 0 }}>
            Tokens, components and patterns for the RCA 3.0 product surface. Built around a warm paper, dark ink, and one signature orange.
          </p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <Btn variant="ghost" icon={<I name="download" size={14}/>}>Tokens.json</Btn>
          <Btn variant="primary" icon={<I name="sparkle" size={14}/>}>Open in product</Btn>
        </div>
      </header>

      {/* COLOR */}
      <Section num="01" title="Color" desc="A small palette by design — warm paper, dark ink, and one orange.">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          <div>
            <CapsLabel style={{ marginBottom: 12 }}>Surfaces</CapsLabel>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10 }}>
              <Swatch name="paper" hex={RCA.paper} role="default surface"/>
              <Swatch name="paper-2" hex={RCA.paper2} role="alt / chip bg"/>
              <Swatch name="white" hex={RCA.white} role="card surface" bord/>
              <Swatch name="paper-3" hex={RCA.paper3} role="hairline" bord/>
            </div>
          </div>
          <div>
            <CapsLabel style={{ marginBottom: 12 }}>Ink</CapsLabel>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10 }}>
              <Swatch name="ink" hex={RCA.ink} role="brand dark" dark/>
              <Swatch name="ink-2" hex={RCA.ink2} role="stroke" dark/>
              <Swatch name="ink-3" hex={RCA.ink3} role="elevated" dark/>
              <Swatch name="ink-4" hex={RCA.ink4} role="border" dark/>
            </div>
          </div>
          <div>
            <CapsLabel style={{ marginBottom: 12 }}>Accent · the one orange</CapsLabel>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 10 }}>
              <Swatch name="accent" hex={RCA.accent} role="brand · always sparingly" big/>
              <Swatch name="accent-h" hex={RCA.accentH} role="hover"/>
              <Swatch name="accent-soft" hex={RCA.accentSoft} role="wash" bord/>
            </div>
          </div>
          <div>
            <CapsLabel style={{ marginBottom: 12 }}>Semantic</CapsLabel>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10 }}>
              <Swatch name="ok" hex={RCA.ok} role="success"/>
              <Swatch name="warn" hex={RCA.warn} role="advisory"/>
              <Swatch name="err" hex={RCA.err} role="error"/>
              <Swatch name="info" hex={RCA.info} role="info"/>
            </div>
          </div>
        </div>
      </Section>

      {/* TYPE */}
      <Section num="02" title="Type" desc="Inter Tight for display, Inter for UI, JetBrains Mono for code & labels.">
        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 32 }}>
          <Card padded={24}>
            <CapsLabel style={{ marginBottom: 16 }}>Display · Inter Tight</CapsLabel>
            <div className="display" style={{ fontSize: 72, marginBottom: 4 }}>Find the root <span style={{ color: RCA.accent }}>cause</span>.</div>
            <div style={{ color: RCA.textPaperD, fontSize: 13 }}>72 / 1.05 / -0.035em</div>
            <div style={{ height: 24 }}/>
            <h2 className="display" style={{ fontSize: 40, marginBottom: 4 }}>Solder voids spiked on Line 3.</h2>
            <div style={{ color: RCA.textPaperD, fontSize: 13 }}>H2 · 40 / 1.1 / -0.03em</div>
            <div style={{ height: 18 }}/>
            <h3 className="display" style={{ fontSize: 22, marginBottom: 4 }}>Top 3 candidate causes</h3>
            <div style={{ color: RCA.textPaperD, fontSize: 13 }}>H3 · 22 / 1.2 / -0.02em</div>
          </Card>
          <Card padded={24}>
            <CapsLabel style={{ marginBottom: 16 }}>Body · Inter</CapsLabel>
            <p style={{ fontSize: 18, lineHeight: 1.55, margin: 0, marginBottom: 6 }}>Lead — analyze the spike, propose the cause.</p>
            <div style={{ color: RCA.textPaperD, fontSize: 12, marginBottom: 18 }}>lead · 18 / 1.55</div>
            <p style={{ fontSize: 14, lineHeight: 1.55, margin: 0, marginBottom: 6 }}>Body — yield correlated with reflow zone-3 temperature drift starting 08-14. Suggest paste-print check first; second-pass on the squeegee pressure log.</p>
            <div style={{ color: RCA.textPaperD, fontSize: 12, marginBottom: 18 }}>body · 14 / 1.55</div>
            <p style={{ fontSize: 12, lineHeight: 1.5, color: RCA.textPaperD, margin: 0, marginBottom: 6 }}>Small — supporting / metadata text.</p>
            <div style={{ color: RCA.textPaperD, fontSize: 12, marginBottom: 18 }}>small · 12 / 1.5</div>
            <div className="mono" style={{ fontSize: 13, color: RCA.ink2 }}>spc.run("reflow_zone3", window="7d")</div>
            <div style={{ color: RCA.textPaperD, fontSize: 12, marginBottom: 18, fontFamily: RCA.fBody }}>mono · 13 · JetBrains Mono</div>
            <div className="caps">section label · uppercase mono</div>
          </Card>
        </div>
      </Section>

      {/* BUTTONS */}
      <Section num="03" title="Buttons" desc="Variants × sizes. Primary is the only orange-filled element by default — use it once per screen.">
        <Card padded={28}>
          <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", rowGap: 18, alignItems: "center", columnGap: 24 }}>
            <CapsLabel>Primary</CapsLabel>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Btn variant="primary" size="sm">Run agent</Btn>
              <Btn variant="primary" size="md" icon={<I name="play" size={14}/>}>Run agent</Btn>
              <Btn variant="primary" size="lg" icon={<I name="sparkle" size={16}/>}>Start investigation</Btn>
            </div>
            <CapsLabel>Solid</CapsLabel>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Btn variant="solid" size="sm">Open</Btn>
              <Btn variant="solid" size="md">Open in editor</Btn>
              <Btn variant="solid" size="md" icon={<I name="arrow_r" size={14}/>}>Continue</Btn>
            </div>
            <CapsLabel>Secondary</CapsLabel>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Btn size="sm">Filter</Btn>
              <Btn icon={<I name="filter" size={14}/>}>Filter</Btn>
              <Btn icon={<I name="download" size={14}/>} iconRight={<I name="chev_d" size={12}/>}>Export</Btn>
            </div>
            <CapsLabel>Ghost</CapsLabel>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Btn variant="ghost" size="sm">Cancel</Btn>
              <Btn variant="ghost" icon={<I name="x" size={14}/>}>Dismiss</Btn>
              <Btn variant="ghost" disabled>Disabled</Btn>
            </div>
          </div>
          <div style={{ height: 20 }}/>
          <div style={{ background: RCA.ink, padding: 20, borderRadius: 8 }}>
            <CapsLabel onDark style={{ marginBottom: 12, color: RCA.textDarkD }}>On dark</CapsLabel>
            <div style={{ display: "flex", gap: 10 }}>
              <Btn variant="primary" icon={<I name="play" size={14}/>}>Run agent</Btn>
              <Btn variant="secondary" onDark>Filter</Btn>
              <Btn variant="ghost" onDark>Cancel</Btn>
              <Btn variant="solid" onDark icon={<I name="arrow_r" size={14}/>}>Continue</Btn>
            </div>
          </div>
        </Card>
      </Section>

      {/* CHIPS / STATUS */}
      <Section num="04" title="Chips · status · tags" desc="Tonal chips for state and metadata. Always pair color with a label — never color alone.">
        <Card padded={24}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <RcaChip dot tone="ok">resolved</RcaChip>
            <RcaChip dot tone="warn">triaging</RcaChip>
            <RcaChip dot tone="err">critical</RcaChip>
            <RcaChip dot tone="accent">awaiting review</RcaChip>
            <RcaChip tone="outline">draft</RcaChip>
            <RcaChip tone="default">notebook</RcaChip>
            <RcaChip tone="default" icon={<I name="users" size={11}/>}>4 members</RcaChip>
            <RcaChip tone="default" icon={<I name="branch" size={11}/>}>feat/zone-3</RcaChip>
            <RcaChip tone="accent" icon={<I name="bug" size={11}/>}>P1 · severity</RcaChip>
            <RcaChip tone="accentSolid" icon={<I name="sparkle" size={11}/>}>agent running</RcaChip>
          </div>
          <div style={{ height: 18 }}/>
          <CapsLabel style={{ marginBottom: 10 }}>Severity scale</CapsLabel>
          <div style={{ display: "flex", gap: 6 }}>
            {["P0 · halt","P1 · critical","P2 · major","P3 · minor","P4 · cosmetic"].map((p, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", border: `1px solid ${RCA.paper3}`, borderRadius: 6, fontSize: 12, color: RCA.textPaper, fontFamily: RCA.fMono }}>
                <StatDot tone={["err","err","warn","warn","mute"][i]}/>{p}
              </div>
            ))}
          </div>
        </Card>
      </Section>

      {/* CARDS */}
      <Section num="05" title="Cards" desc="Light & dark surface cards used across the product.">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          {/* investigation card */}
          <Card padded={18}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <RcaChip dot tone="err">P1 · critical</RcaChip>
              <I name="pin" size={14} color={RCA.textPaperD2}/>
            </div>
            <div className="mono" style={{ fontSize: 11, color: RCA.textPaperD2, marginBottom: 4 }}>Line 3 · MX-7</div>
            <h3 className="display" style={{ fontSize: 18, marginBottom: 8 }}>Solder voids spike <span style={{ color: RCA.accent }}>·</span> Line 3</h3>
            <p style={{ fontSize: 13, color: RCA.textPaperD, margin: 0, marginBottom: 14, lineHeight: 1.45 }}>Void rate 2.3× baseline since 08-14 14:00, correlated with reflow zone-3 drift.</p>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderTop: `1px dashed ${RCA.paper3}`, paddingTop: 10 }}>
              <div style={{ display: "flex", gap: 4 }}><Avatar name="AC" size={22}/><Avatar name="BL" size={22}/><Avatar name="+2" size={22}/></div>
              <Sparkline w={70}/>
            </div>
          </Card>
          {/* metric card */}
          <Card padded={18}>
            <CapsLabel style={{ marginBottom: 8 }}>Open investigations</CapsLabel>
            <div className="display" style={{ fontSize: 44, marginBottom: 4 }}>14</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: RCA.textPaperD, fontSize: 12 }}>
              <RcaChip tone="ok"><I name="arrow_d" size={10}/> -3 vs last wk</RcaChip>
              <span>4 P1 · 7 P2 · 3 P3</span>
            </div>
            <div style={{ height: 16 }}/>
            <Hatch2 h={64} label="trend · open investigations · 4w"/>
          </Card>
          {/* dark variant */}
          <Card padded={18} onDark style={{ color: RCA.textDark }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <RcaChip dot tone="accentSolid" onDark>agent running</RcaChip>
              <I name="dots_h" size={14} color={RCA.textDarkD}/>
            </div>
            <div className="mono" style={{ fontSize: 11, color: RCA.textDarkD2, marginBottom: 4 }}>step 4 / 6</div>
            <h3 className="display" style={{ fontSize: 18, marginBottom: 8, color: RCA.textDark }}>Correlating sensor logs with defect timeline…</h3>
            <p style={{ fontSize: 12, color: RCA.textDarkD, margin: 0, marginBottom: 14, lineHeight: 1.5, fontFamily: RCA.fMono }}>find_correlation(target="void_rate", window="7d", min=0.5)</p>
            <div style={{ display: "flex", gap: 8 }}>
              <Btn variant="solid" size="sm" onDark>Pause</Btn>
              <Btn variant="ghost" size="sm" onDark>View plan</Btn>
            </div>
          </Card>
        </div>
      </Section>

      {/* INPUTS */}
      <Section num="06" title="Inputs" desc="Text, search, select.">
        <Card padded={24}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
            <div>
              <CapsLabel style={{ marginBottom: 6 }}>Search</CapsLabel>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 12px", height: 38, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                <I name="search" size={15} color={RCA.textPaperD2}/>
                <span style={{ color: RCA.textPaperD2, fontSize: 13, flex: 1 }}>Search investigations…</span>
                <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2, padding: "2px 6px", border: `1px solid ${RCA.paper3}`, borderRadius: 4 }}>⌘K</span>
              </div>
            </div>
            <div>
              <CapsLabel style={{ marginBottom: 6 }}>Text field</CapsLabel>
              <div style={{ display: "flex", alignItems: "center", padding: "0 12px", height: 38, background: RCA.white, border: `1.5px solid ${RCA.accent}`, borderRadius: 6 }}>
                <span style={{ fontSize: 13 }}>Solder voids · Line 3</span>
                <span style={{ width: 1.5, height: 16, background: RCA.accent, marginLeft: 2 }}/>
              </div>
            </div>
            <div>
              <CapsLabel style={{ marginBottom: 6 }}>Select</CapsLabel>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 12px", height: 38, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
                <span style={{ fontSize: 13, flex: 1 }}>Severity: <strong>P1 · critical</strong></span>
                <I name="chev_d" size={14} color={RCA.textPaperD}/>
              </div>
            </div>
            <div>
              <CapsLabel style={{ marginBottom: 6 }}>Chat composer</CapsLabel>
              <div style={{ background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, padding: 10, gridColumn: "span 3" }}>
                <div style={{ fontSize: 14, color: RCA.textPaper }}>Compare zone-3 temp profile against last week's baseline and flag any drift above 3°C.</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10 }}>
                  <Btn size="sm" variant="ghost" icon={<I name="plus" size={13}/>}>Attach data</Btn>
                  <RcaChip tone="default" icon={<I name="file" size={11}/>}>@ reflow_zone3.csv</RcaChip>
                  <div style={{ flex: 1 }}/>
                  <span className="mono" style={{ fontSize: 11, color: RCA.textPaperD2 }}>⌘↵ to send</span>
                  <Btn size="sm" variant="primary" icon={<I name="arrow_r" size={13}/>}>Ask agent</Btn>
                </div>
              </div>
            </div>
          </div>
        </Card>
      </Section>

      {/* LOGO USES */}
      <Section num="07" title="Logo · usage" desc="Mark, lockup, and the small orange square that replaces the dot in the title-cased lockup.">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          <Card padded={28} style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16 }}>
            <RCAMark size={100}/>
            <CapsLabel>Mark · light bg</CapsLabel>
          </Card>
          <Card padded={28} onDark style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16, background: RCA.ink }}>
            <RCAMark size={100} color={RCA.textDark}/>
            <CapsLabel onDark style={{ color: RCA.textDarkD }}>Mark · dark bg</CapsLabel>
          </Card>
          <Card padded={28} style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16 }}>
            <RCALockup size={36}/>
            <CapsLabel>Horizontal lockup</CapsLabel>
          </Card>
        </div>
      </Section>

      {/* SPACING / RADII */}
      <Section num="08" title="Geometry" desc="Spacing scale, radii, hairlines.">
        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
          <Card padded={24}>
            <CapsLabel style={{ marginBottom: 12 }}>Spacing (4px base)</CapsLabel>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 12 }}>
              {[4, 8, 12, 16, 24, 32, 48, 64].map((n) => (
                <div key={n} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                  <div style={{ width: n, height: n, background: RCA.ink2 }}/>
                  <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD }}>{n}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card padded={24}>
            <CapsLabel style={{ marginBottom: 12 }}>Radii</CapsLabel>
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              {[
                ["4", 4, "chip"],
                ["6", 6, "btn / input"],
                ["8", 8, "card"],
                ["12", 12, "modal"],
                ["50%", "50%", "avatar"],
              ].map((r, i) => (
                <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 40, height: 40, background: RCA.ink, borderRadius: r[1] }}/>
                  <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD }}>{r[0]}</span>
                  <span style={{ fontSize: 10, color: RCA.textPaperD2 }}>{r[2]}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </Section>

      {/* PRINCIPLES */}
      <Section num="09" title="Principles" desc="When in doubt.">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
          {[
            ["Quiet by default", "The orange is for one thing on screen — the action, the answer, the agent. If it appears twice, decide which is more important."],
            ["Mono speaks data", "Tag IDs, sensor names, code, timestamps — anything machine-shaped — use mono. Human prose uses Inter."],
            ["Plain paper, no shadow", "Cards are hairlines on paper. No drop shadows. Elevation comes from contrast, not depth."],
            ["Show the agent's hands", "Tool calls are visible by default. Hide them only when the user explicitly collapses."],
            ["One investigation per screen", "Resist tabs of unrelated incidents. Split when comparing; otherwise focus."],
            ["Defects are real", "Photos, logs, and waveforms. Show the artifact, not just numbers."],
          ].map(([t, d], i) => (
            <Card padded={20} key={i}>
              <div className="mono" style={{ fontSize: 12, color: RCA.accent, marginBottom: 8 }}>· {String(i+1).padStart(2,"0")}</div>
              <h3 className="display" style={{ fontSize: 18, marginBottom: 6 }}>{t}</h3>
              <p style={{ fontSize: 13, color: RCA.textPaperD, margin: 0, lineHeight: 1.5 }}>{d}</p>
            </Card>
          ))}
        </div>
      </Section>

      <footer style={{ borderTop: `1px solid ${RCA.paper3}`, paddingTop: 24, display: "flex", justifyContent: "space-between", alignItems: "center", color: RCA.textPaperD, fontSize: 12 }}>
        <span>RCA 3.0 Design System · v1</span>
        <span className="mono">{new Date().toISOString().slice(0,10)}</span>
      </footer>
    </div>
  );
}

function Section({ num, title, desc, children }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
        <span className="mono" style={{ fontSize: 12, color: RCA.accent, fontWeight: 500 }}>· {num}</span>
        <h2 className="display" style={{ fontSize: 28 }}>{title}</h2>
        <span style={{ color: RCA.textPaperD, fontSize: 13, marginLeft: 12, maxWidth: 520 }}>{desc}</span>
      </div>
      {children}
    </section>
  );
}

function Swatch({ name, hex, role, dark, big, bord }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{
        background: hex, height: big ? 86 : 64, borderRadius: 6,
        border: bord ? `1px solid ${RCA.paper3}` : "none",
        color: dark ? RCA.textDark : RCA.ink,
        position: "relative",
        padding: 10,
        display: "flex", alignItems: "flex-end",
      }}>
        <span className="mono" style={{ fontSize: 11, fontWeight: 500 }}>{hex}</span>
      </div>
      <div>
        <div className="mono" style={{ fontSize: 11, fontWeight: 500 }}>{name}</div>
        <div style={{ fontSize: 11, color: RCA.textPaperD }}>{role}</div>
      </div>
    </div>
  );
}

Object.assign(window, { DSScreen });
