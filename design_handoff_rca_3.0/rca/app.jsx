// RCA 3.0 prototype app — Home + Investigation + KB Drawer + KB page + Chats page
// Mount: <RCAApp/>

function RCAApp() {
  const [route, setRoute] = React.useState({ view: "home", id: null });
  const [newOpen, setNewOpen] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);

  const openInvestigation = (id) => setRoute({ view: "investigation", id });
  const backHome = () => setRoute({ view: "home", id: null });
  const openKB = () => { setDrawerOpen(false); setRoute({ view: "kb" }); };
  const openChats = () => { setDrawerOpen(false); setRoute({ view: "chats" }); };

  return (
    <div style={{ position: "fixed", inset: 0, background: RCA.paper, overflow: "hidden" }}>
      {route.view === "home" && (
        <div style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "stretch", overflow: "auto" }}>
          <div style={{ width: HOME_W, minWidth: HOME_W, height: HOME_H, alignSelf: "center", boxShadow: "0 6px 40px rgba(20,22,28,.08)", borderRadius: 8, overflow: "hidden", border: `1px solid ${RCA.paper3}` }}>
            <HomeRCA
              onSelect={openInvestigation}
              onNew={() => setNewOpen(true)}
              onAskAgent={() => setDrawerOpen(true)}
              onOpenKB={openKB}
              onOpenChats={openChats}
            />
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
      {route.view === "kb" && (
        <div style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "stretch", overflow: "auto" }}>
          <div style={{ width: 1440, minWidth: 1440, height: 900, alignSelf: "center", boxShadow: "0 6px 40px rgba(20,22,28,.08)", borderRadius: 8, overflow: "hidden", border: `1px solid ${RCA.paper3}`, position: "relative" }}>
            <KBPage onBack={backHome} onAskAgent={() => setDrawerOpen(true)}/>
          </div>
        </div>
      )}
      {route.view === "chats" && (
        <div style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "stretch", overflow: "auto" }}>
          <div style={{ width: 1440, minWidth: 1440, height: 900, alignSelf: "center", boxShadow: "0 6px 40px rgba(20,22,28,.08)", borderRadius: 8, overflow: "hidden", border: `1px solid ${RCA.paper3}` }}>
            <ChatsPage onBack={backHome} onOpenChat={() => setDrawerOpen(true)} onAskAgent={() => setDrawerOpen(true)}/>
          </div>
        </div>
      )}
      {newOpen && (
        <NewInvestigation
          onClose={() => setNewOpen(false)}
          onCreate={() => { setNewOpen(false); openInvestigation("INC-2026-0143"); }}
        />
      )}
      <KBDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onOpenKB={openKB}
        onOpenChats={openChats}
      />

      {/* prototype hint */}
      <div style={{ position: "fixed", left: 16, bottom: 16, padding: "8px 12px", background: RCA.ink, color: RCA.textDark, borderRadius: 6, fontSize: 11, fontFamily: RCA.fMono, display: "flex", alignItems: "center", gap: 8, opacity: 0.85, zIndex: 70 }}>
        <I name="sparkle" size={12} color={RCA.accent}/>
        <span>RCA 3.0 · clickable prototype</span>
        <span style={{ color: RCA.textDarkD }}>·</span>
        <span>
          {route.view === "home" && "click any row · Ask agent → drawer"}
          {route.view === "investigation" && "tabs switch views · chips drive agent"}
          {route.view === "kb" && "knowledge base management"}
          {route.view === "chats" && "chat history"}
        </span>
      </div>
    </div>
  );
}

Object.assign(window, { RCAApp });
