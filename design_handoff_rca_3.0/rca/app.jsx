// RCA 3.0 prototype app — wires Home + Investigation + New Investigation modal
// Mount: <RCAApp/>

function RCAApp() {
  const [route, setRoute] = React.useState({ view: "home", id: null });
  const [newOpen, setNewOpen] = React.useState(false);

  const openInvestigation = (id) => setRoute({ view: "investigation", id });
  const backHome = () => setRoute({ view: "home", id: null });

  return (
    <div style={{ position: "fixed", inset: 0, background: RCA.paper, overflow: "hidden" }}>
      {route.view === "home" && (
        <div style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "stretch", overflow: "auto" }}>
          <div style={{ width: HOME_W, minWidth: HOME_W, height: HOME_H, alignSelf: "center", boxShadow: "0 6px 40px rgba(20,22,28,.08)", borderRadius: 8, overflow: "hidden", border: `1px solid ${RCA.paper3}` }}>
            <HomeRCA onSelect={openInvestigation} onNew={() => setNewOpen(true)}/>
          </div>
        </div>
      )}
      {route.view === "investigation" && (
        <div style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "stretch", overflow: "auto" }}>
          <div style={{ width: INV_W, minWidth: INV_W, height: INV_H, alignSelf: "center", boxShadow: "0 6px 40px rgba(20,22,28,.08)", borderRadius: 8, overflow: "hidden", border: `1px solid ${RCA.paper3}` }}>
            <InvestigationRCA onBack={backHome} key={route.id}/>
          </div>
        </div>
      )}
      {newOpen && (
        <NewInvestigation
          onClose={() => setNewOpen(false)}
          onCreate={() => { setNewOpen(false); openInvestigation("INC-2026-0143"); }}
        />
      )}

      {/* prototype hint, bottom-left */}
      <div style={{ position: "fixed", left: 16, bottom: 16, padding: "8px 12px", background: RCA.ink, color: RCA.textDark, borderRadius: 6, fontSize: 11, fontFamily: RCA.fMono, display: "flex", alignItems: "center", gap: 8, opacity: 0.85 }}>
        <I name="sparkle" size={12} color={RCA.accent}/>
        <span>RCA 3.0 · clickable prototype</span>
        <span style={{ color: RCA.textDarkD }}>·</span>
        <span>{route.view === "home" ? "click any row" : "tabs switch views · chips drive agent"}</span>
      </div>
    </div>
  );
}

Object.assign(window, { RCAApp });
