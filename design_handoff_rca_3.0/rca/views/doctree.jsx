// Documents tab — VSCode-shaped file tree + editable "Monaco" editor.
// Exposes: DocTreeView({ docs, isAuto, collection, onOpenSource })
//
// Manual collections are a real file workspace: drag files/folders onto the
// Explorer to upload, click Edit to change a text file, Save to re-index,
// right-click (or ⋮) to rename / download / delete, + buttons to add. The
// agent "re-indexes" changed files (indexing… → indexed). All mutations
// persist to localStorage per collection. Auto collections (agent-managed
// investigation archives) stay read-only browse.

// ---- seed text for the text-kind files -----------------------------------
const DOC_TEXT = {
  "change-log-2025-q3.md": `# Change log · 2025 Q3

## 2025-08-12 · Reflow zone-3 PID retune
Retuned after observing 3.2 degC drift on MX-7.
New gains: Kp = 1.8, Ki = 0.4, Kd = 0.08.
See INC-0119 for the full root-cause analysis.

## 2025-08-04 · AOI threshold review
Lowered void-rate alarm threshold from 2.5% to 2.0%
in line with updated IPC-A-610 guidance.
Rolling 7-day baseline is now baseline-anchored, not absolute.

## 2025-07-22 · Change-control matrix update
Added throughput aggregation as a tracked process change.
- Owner: process eng on-call
- Trigger: aggregated throughput > 8% of nominal / 24 h
- SPC: zone-actual vs set-point delta > 2 degC / 15 min`,

  "shift-handover-template.md": `# Shift handover — SMT Line 3

> Fill this out at the end of every shift. Keep it factual.

## Lines & equipment
- [ ] MX-7 reflow: profile nominal? last retune?
- [ ] AOI: open defects flagged this shift
- [ ] Printer: paste lot + remaining life

## Open investigations
- INC-____  ·  owner: ____  ·  next step: ____

## Watch items for next shift
1.
2.
3.

_Signed:_ ____________   _Shift:_ A / B / C`,

  "zone3-profile.csv": `zone,set_c,soak_s,ramp_c_s,phase
1,120,30,1.5,preheat
2,180,40,1.0,preheat-soak
3,245,52,0.7,soak-reflow
4,245,20,0.0,reflow-plateau
5,120,30,-2.0,cool`,

  "spc-alarm.log": `2025-12-04 14:02:11  INFO   spc.read zone=3 actual=244.8 set=245.0 dt=-0.2
2025-12-04 14:14:39  INFO   spc.read zone=3 actual=243.1 set=245.0 dt=-1.9
2025-12-04 14:21:07  WARN   zone-3 delta exceeded 2.0 degC over 15 min window
2025-12-04 14:21:07  WARN   defects.aoi void_rate=2.6% baseline=2.0%
2025-12-04 14:33:50  ERROR  void_rate=3.2% sustained across 4 shifts
2025-12-04 14:34:02  INFO   correlate.find throughput~temp r=0.88 p<0.01
2025-12-04 14:40:18  INFO   investigation INC-0119 opened by alice.chen`,

  "doe-config.json": `{
  "experiment": "throughput-vs-reflow-temp",
  "factors": {
    "throughput_bpm": [40, 55, 70],
    "paste_lot": ["PA-25-W12", "PA-25-W14"],
    "ambient_rh_pct": 45
  },
  "response": "zone3_actual_c",
  "replicates": 3,
  "randomize": true,
  "owner": "bob.liu"
}`,
};

const TEXT_KINDS = ["md", "csv", "log", "json", "txt", "yaml"];
const KIND_LANG = { md: "Markdown", csv: "CSV", log: "Log", json: "JSON", txt: "Plain text", yaml: "YAML", pdf: "PDF", doc: "Word", sheet: "Spreadsheet", image: "Image", investigation: "Report" };
const EXT_KIND = { md: "md", markdown: "md", csv: "csv", log: "log", json: "json", txt: "txt", text: "txt", yaml: "yaml", yml: "yaml", pdf: "pdf", doc: "doc", docx: "doc", xls: "sheet", xlsx: "sheet", png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image" };
const TODAY = "2026-06-12";

function extKind(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return EXT_KIND[ext] || "txt";
}
function docIcon(kind) {
  return kind === "investigation" ? "bug" : (kind === "sheet" || kind === "csv") ? "table" : kind === "image" ? "photo" : "file";
}
function fmtSize(bytes) {
  if (bytes == null) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(bytes < 10240 ? 1 : 0) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

// ---- build a nested folder tree from entries + explicit folders ----------
function buildTree(entries, folderSet) {
  const root = { name: "", type: "folder", path: "", children: [] };
  const ensureFolder = (parts) => {
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const seg = parts[i];
      let child = node.children.find((c) => c.type === "folder" && c.name === seg);
      if (!child) { child = { name: seg, type: "folder", path: parts.slice(0, i + 1).join("/"), children: [] }; node.children.push(child); }
      node = child;
    }
    return node;
  };
  (folderSet ? [...folderSet] : []).forEach((p) => p && ensureFolder(p.split("/")));
  entries.forEach((d) => {
    const parts = (d.path || d.title).split("/");
    const parent = parts.length > 1 ? ensureFolder(parts.slice(0, -1)) : root;
    parent.children.push({ name: parts[parts.length - 1], type: "file", path: d.path || d.title, doc: d });
  });
  const sort = (n) => {
    n.children.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "folder" ? -1 : 1));
    n.children.forEach((c) => c.type === "folder" && sort(c));
  };
  sort(root);
  return root;
}
function countFiles(node) {
  return node.children.reduce((s, c) => s + (c.type === "folder" ? countFiles(c) : 1), 0);
}

// ---- syntax tinting (restrained, editor-scoped) --------------------------
function tintJSON(line) {
  const out = [];
  const re = /("(?:[^"\\]|\\.)*"\s*:)|("(?:[^"\\]|\\.)*")|(-?\d+\.?\d*)|(\btrue\b|\bfalse\b|\bnull\b)|([{}\[\],:])/g;
  let last = 0, m;
  while ((m = re.exec(line))) {
    if (m.index > last) out.push({ t: line.slice(last, m.index) });
    if (m[1]) out.push({ t: m[1], c: RCA.ink, b: 600 });
    else if (m[2]) out.push({ t: m[2], c: RCA.ok });
    else if (m[3]) out.push({ t: m[3], c: RCA.info });
    else if (m[4]) out.push({ t: m[4], c: RCA.info });
    else out.push({ t: m[5], c: RCA.textPaperD2 });
    last = re.lastIndex;
  }
  if (last < line.length) out.push({ t: line.slice(last) });
  return out;
}
function renderLine(line, lang) {
  if (lang === "json") {
    return tintJSON(line).map((s, i) => <span key={i} style={{ color: s.c || RCA.ink2, fontWeight: s.b || 400 }}>{s.t}</span>);
  }
  if (lang === "log") {
    const m = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(INFO|WARN|ERROR)\s+(.*)$/);
    if (m) {
      const lvl = m[2] === "ERROR" ? RCA.err : m[2] === "WARN" ? RCA.warn : RCA.ok;
      return [
        <span key="t" style={{ color: RCA.textPaperD2 }}>{m[1]} </span>,
        <span key="l" style={{ color: lvl, fontWeight: 600 }}>{m[2].padEnd(6)}</span>,
        <span key="b" style={{ color: RCA.ink2 }}>{m[3]}</span>,
      ];
    }
    return <span style={{ color: RCA.ink2 }}>{line}</span>;
  }
  if (lang === "csv") {
    const cells = line.split(",");
    return cells.map((c, i) => (
      <React.Fragment key={i}>
        {i > 0 && <span style={{ color: RCA.textPaperD2 }}>,</span>}
        <span style={{ color: RCA.ink2 }}>{c}</span>
      </React.Fragment>
    ));
  }
  if (lang === "md") {
    if (/^(#{1,6})\s+/.test(line)) return <span style={{ color: RCA.ink, fontWeight: 700 }}>{line}</span>;
    if (/^\s*>/.test(line)) return <span style={{ color: RCA.textPaperD, fontStyle: "italic" }}>{line}</span>;
    const li = line.match(/^(\s*)([-*]|\d+\.|\[.\])\s+(.*)$/);
    if (li) return [<span key="b" style={{ color: RCA.accent }}>{li[1]}{li[2]} </span>, <span key="t" style={{ color: RCA.ink2 }}>{li[3]}</span>];
    if (line.startsWith("_") && line.endsWith("_") && line.length > 1) return <span style={{ color: RCA.textPaperD, fontStyle: "italic" }}>{line}</span>;
    return <span style={{ color: RCA.ink2 }}>{line}</span>;
  }
  return <span style={{ color: RCA.ink2 }}>{line}</span>;
}

// ---- editor pane: read (highlighted) OR edit (textarea) ------------------
function EditorPane({ entry, editing, draft, setDraft }) {
  const text = editing ? draft : (entry.content || "");
  const lines = text.split("\n");
  const lang = entry.kind;
  const [active, setActive] = React.useState(null);
  const gutterRef = React.useRef(null);
  const taRef = React.useRef(null);
  React.useEffect(() => { if (editing && taRef.current) taRef.current.focus(); }, [editing, entry.path]);
  const onScroll = () => { if (gutterRef.current && taRef.current) gutterRef.current.scrollTop = taRef.current.scrollTop; };

  if (editing) {
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", background: RCA.white, overflow: "hidden" }}>
        <div ref={gutterRef} style={{ flexShrink: 0, padding: "12px 0", textAlign: "right", userSelect: "none", background: RCA.paper, borderRight: `1px solid ${RCA.paper3}`, overflow: "hidden" }}>
          {lines.map((_, i) => (
            <div key={i} style={{ height: 21, padding: "0 14px 0 16px", fontFamily: RCA.fMono, fontSize: 12, lineHeight: "21px", color: RCA.textPaperD2 }}>{i + 1}</div>
          ))}
          <div style={{ height: 40 }}/>
        </div>
        <textarea ref={taRef} value={draft} onChange={(e) => setDraft(e.target.value)} onScroll={onScroll} spellCheck={false} wrap="off"
          style={{ flex: 1, minWidth: 0, border: "none", outline: "none", resize: "none", padding: "12px 24px 40px 8px", margin: 0, fontFamily: RCA.fMono, fontSize: 12.5, lineHeight: "21px", color: RCA.ink2, background: RCA.white, whiteSpace: "pre", overflow: "auto", caretColor: RCA.accent }} className="scrollable"/>
      </div>
    );
  }
  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", background: RCA.white, overflow: "auto" }} className="scrollable">
      <div style={{ flexShrink: 0, padding: "12px 0", textAlign: "right", userSelect: "none", background: RCA.white, position: "sticky", left: 0 }}>
        {lines.map((_, i) => (
          <div key={i} onMouseEnter={() => setActive(i)} style={{ height: 21, padding: "0 16px 0 18px", fontFamily: RCA.fMono, fontSize: 12, lineHeight: "21px", color: active === i ? RCA.ink : RCA.textPaperD2, background: active === i ? RCA.paper2 : "transparent" }}>{i + 1}</div>
        ))}
      </div>
      <div style={{ flex: 1, padding: "12px 0", minWidth: 0 }}>
        {lines.map((ln, i) => (
          <div key={i} onMouseEnter={() => setActive(i)} style={{ minHeight: 21, padding: "0 24px 0 8px", fontFamily: RCA.fMono, fontSize: 12.5, lineHeight: "21px", whiteSpace: "pre", background: active === i ? RCA.paper2 : "transparent", borderLeft: `2px solid ${active === i ? RCA.paper3 : "transparent"}` }}>
            {ln.length ? renderLine(ln, lang) : "\u200b"}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- tiny context menu ----------------------------------------------------
function CtxMenu({ x, y, items, onClose }) {
  React.useEffect(() => {
    const h = () => onClose();
    window.addEventListener("click", h);
    window.addEventListener("contextmenu", h);
    return () => { window.removeEventListener("click", h); window.removeEventListener("contextmenu", h); };
  }, []);
  return (
    <div style={{ position: "fixed", left: Math.min(x, window.innerWidth - 180), top: Math.min(y, window.innerHeight - items.length * 34 - 10), zIndex: 100, minWidth: 168, background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 8, padding: 5, boxShadow: "0 8px 28px rgba(22,24,29,0.14)" }} onClick={(e) => e.stopPropagation()}>
      {items.map((it, i) => it.sep ? (
        <div key={i} style={{ height: 1, background: RCA.paper3, margin: "5px 4px" }}/>
      ) : (
        <div key={i} onClick={() => { onClose(); it.onClick(); }} style={{ display: "flex", alignItems: "center", gap: 9, height: 30, padding: "0 9px", borderRadius: 5, cursor: "pointer", fontSize: 13, color: it.danger ? RCA.err : RCA.ink }}
          onMouseEnter={(e) => (e.currentTarget.style.background = it.danger ? "rgba(196,74,58,0.08)" : RCA.paper2)} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <I name={it.icon} size={14} color={it.danger ? RCA.err : RCA.textPaperD}/>{it.label}
        </div>
      ))}
    </div>
  );
}

// ---- tree node (recursive) ------------------------------------------------
function TreeNode({ node, depth, openFolders, toggle, selectedPath, onSelect, editable, renaming, setRenaming, onRename, onCtx, dropTarget, setDropTarget }) {
  const pad = depth * 14 + 8;
  const isRenaming = renaming === node.path;
  const [draftName, setDraftName] = React.useState(node.name);
  React.useEffect(() => { if (isRenaming) setDraftName(node.name); }, [isRenaming]);

  const renameBox = (
    <input autoFocus value={draftName} onClick={(e) => e.stopPropagation()} onChange={(e) => setDraftName(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Enter") { onRename(node, draftName); setRenaming(null); } if (e.key === "Escape") setRenaming(null); }}
      onBlur={() => { onRename(node, draftName); setRenaming(null); }}
      style={{ flex: 1, minWidth: 0, height: 21, border: `1px solid ${RCA.accent}`, borderRadius: 3, padding: "0 5px", fontFamily: RCA.fMono, fontSize: 12.5, color: RCA.ink, outline: "none", background: RCA.white }}/>
  );

  if (node.type === "folder") {
    const open = openFolders.has(node.path);
    const isDrop = dropTarget === node.path;
    return (
      <div>
        <div onClick={() => toggle(node.path)} onContextMenu={(e) => editable && onCtx(e, node)}
          onDragOver={editable ? (e) => { e.preventDefault(); e.stopPropagation(); setDropTarget(node.path); } : undefined}
          onDragLeave={editable ? () => setDropTarget((p) => (p === node.path ? null : p)) : undefined}
          style={{ display: "flex", alignItems: "center", gap: 6, height: 28, padding: `0 8px 0 ${pad}px`, cursor: "pointer", color: RCA.textPaper, userSelect: "none", background: isDrop ? RCA.accentSoft : "transparent", borderLeft: `2px solid ${isDrop ? RCA.accent : "transparent"}` }}
          onMouseEnter={(e) => { if (!isDrop) e.currentTarget.style.background = RCA.paper2; }} onMouseLeave={(e) => { if (!isDrop) e.currentTarget.style.background = "transparent"; }}>
          <I name={open ? "chev_d" : "chev_r"} size={12} color={RCA.textPaperD}/>
          <I name="folder" size={14} color={RCA.textPaperD}/>
          {isRenaming ? renameBox : <span style={{ fontSize: 13, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>}
          {!isRenaming && <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD2, marginLeft: "auto" }}>{countFiles(node)}</span>}
        </div>
        {open && node.children.map((c, i) => (
          <TreeNode key={c.path + i} node={c} depth={depth + 1} openFolders={openFolders} toggle={toggle} selectedPath={selectedPath} onSelect={onSelect} editable={editable} renaming={renaming} setRenaming={setRenaming} onRename={onRename} onCtx={onCtx} dropTarget={dropTarget} setDropTarget={setDropTarget}/>
        ))}
      </div>
    );
  }
  const on = selectedPath === node.path;
  const cited = node.doc.cited ?? 0;
  const status = node.doc.status;
  return (
    <div onClick={() => onSelect(node.doc)} onContextMenu={(e) => editable && onCtx(e, node)}
      style={{ display: "flex", alignItems: "center", gap: 6, height: 28, padding: `0 8px 0 ${pad}px`, cursor: "pointer", background: on ? RCA.accentSoft : "transparent", borderLeft: `2px solid ${on ? RCA.accent : "transparent"}`, color: on ? RCA.accentH : RCA.textPaper, userSelect: "none" }}
      onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = RCA.paper2; }} onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = "transparent"; }}>
      <span style={{ width: 12 }}/>
      <I name={docIcon(node.doc.kind)} size={13} color={on ? RCA.accentH : RCA.textPaperD}/>
      {isRenaming ? renameBox : <span style={{ fontSize: 13, fontWeight: on ? 600 : 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: 1 }}>{node.name}</span>}
      {!isRenaming && status === "indexing" && <Spinner/>}
      {!isRenaming && status !== "indexing" && cited > 0 && <span className="mono" style={{ fontSize: 10, color: on ? RCA.accentH : RCA.accent, fontWeight: 600 }}>{cited}×</span>}
    </div>
  );
}

function Spinner() {
  return <span style={{ width: 11, height: 11, borderRadius: "50%", border: `1.5px solid ${RCA.paper3}`, borderTopColor: RCA.accent, display: "inline-block", animation: "rcaspin 0.7s linear infinite" }}/>;
}

// ---- main split view ------------------------------------------------------
function DocTreeView({ docs, isAuto, collection, onOpenSource, storePrefix = "rca-docs-", directEdit = false, sourcesByPath = null, onDropFiles = null, dropLabel = "Drop files or a folder here", dropSub = "or click to browse", defaultPath = null }) {
  const editable = !isAuto;
  const dEdit = directEdit && editable;
  const storeKey = storePrefix + (collection.id || collection.title);

  // seed entries (flat) + explicit folders
  const seed = React.useMemo(() => docs.map((d) => ({
    path: d.path || d.title, kind: d.kind, content: d.content != null ? d.content : (TEXT_KINDS.includes(d.kind) ? (DOC_TEXT[d.title] || "") : null),
    chunks: d.chunks, cited: d.cited ?? 0, updated: d.updated, by: d.by, size: d.size, status: "indexed",
  })), [docs]);

  const [entries, setEntries] = React.useState(() => {
    if (editable) { try { const s = JSON.parse(localStorage.getItem(storeKey)); if (s && s.entries) return s.entries; } catch (e) {} }
    return seed;
  });
  const [folders, setFolders] = React.useState(() => {
    if (editable) { try { const s = JSON.parse(localStorage.getItem(storeKey)); if (s && s.folders) return s.folders; } catch (e) {} }
    return [];
  });
  React.useEffect(() => {
    if (editable) { try { localStorage.setItem(storeKey, JSON.stringify({ entries, folders })); } catch (e) {} }
  }, [entries, folders, editable, storeKey]);

  const tree = React.useMemo(() => buildTree(entries, folders), [entries, folders]);
  const allFolders = React.useMemo(() => { const acc = []; const walk = (n) => n.children.forEach((c) => { if (c.type === "folder") { acc.push(c.path); walk(c); } }); walk(tree); return acc; }, [tree]);
  const firstFile = React.useMemo(() => { let f = null; const walk = (n) => n.children.forEach((c) => { if (f) return; c.type === "file" ? (f = c.doc) : walk(c); }); walk(tree); return f; }, [tree]);

  const [openFolders, setOpenFolders] = React.useState(() => new Set(allFolders));
  const [selectedPath, setSelectedPath] = React.useState(
    defaultPath && entries.some((e) => e.path === defaultPath) ? defaultPath : (firstFile ? firstFile.path : null)
  );
  const [query, setQuery] = React.useState("");
  const [editing, setEditing] = React.useState(dEdit);
  const [draft, setDraft] = React.useState("");
  const [renaming, setRenaming] = React.useState(null);
  const [ctx, setCtx] = React.useState(null);
  const [dropTarget, setDropTarget] = React.useState(null);
  const [dragActive, setDragActive] = React.useState(false);
  const [toast, setToast] = React.useState(null);
  const fileInputRef = React.useRef(null);
  const pendingFolder = React.useRef("");

  const selected = React.useMemo(() => entries.find((e) => e.path === selectedPath) || null, [entries, selectedPath]);
  React.useEffect(() => { if (!selected && firstFile) setSelectedPath(firstFile.path); }, [selected, firstFile]);
  const draftRef = React.useRef("");
  React.useEffect(() => { draftRef.current = draft; }, [draft]);
  React.useEffect(() => {
    if (dEdit) {
      const cur = entries.find((e) => e.path === selectedPath);
      setDraft(cur && cur.content != null ? cur.content : "");
      setEditing(true);
    } else {
      setEditing(false);
    }
    const prev = selectedPath;
    return () => {
      if (dEdit && prev) {
        setEntries((es) => es.map((e) => (e.path === prev && e.content != null && e.content !== draftRef.current) ? { ...e, content: draftRef.current, size: fmtSize(new Blob([draftRef.current]).size), updated: TODAY } : e));
      }
    };
  }, [selectedPath]);

  const toggle = (p) => setOpenFolders((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });
  const collapseAll = () => setOpenFolders(new Set());
  const flash = (msg) => { setToast(msg); setTimeout(() => setToast((t) => (t === msg ? null : t)), 2200); };

  // re-index a path: indexing… then indexed
  const reindex = (path) => {
    setEntries((es) => es.map((e) => e.path === path ? { ...e, status: "indexing" } : e));
    setTimeout(() => setEntries((es) => es.map((e) => e.path === path ? { ...e, status: "indexed", updated: TODAY } : e)), 1400);
  };

  const handleUpload = (files, folder) => { if (onDropFiles) onDropFiles(files); else addFiles(files, folder); };

  // ---- mutations ----
  const uniquePath = (base) => { let p = base, i = 2; const has = (x) => entries.some((e) => e.path === x); const dot = base.lastIndexOf("."); while (has(p)) { p = dot > base.lastIndexOf("/") ? base.slice(0, dot) + "-" + i + base.slice(dot) : base + "-" + i; i++; } return p; };

  const addFiles = (fileList, targetFolder) => {
    const arr = [...fileList];
    if (!arr.length) return;
    let added = 0;
    arr.forEach((file) => {
      const rel = file.webkitRelativePath || file.name;
      const path = uniquePath((targetFolder ? targetFolder + "/" : "") + rel);
      const kind = extKind(file.name);
      const finish = (content) => {
        setEntries((es) => [...es, { path, kind, content, chunks: 0, cited: 0, updated: TODAY, by: "You", size: fmtSize(file.size), status: "indexing" }]);
        setTimeout(() => setEntries((es) => es.map((e) => e.path === path ? { ...e, status: "indexed", chunks: Math.max(1, Math.round((file.size || 800) / 1400)) } : e)), 1500);
      };
      if (TEXT_KINDS.includes(kind) && file.size < 400000) {
        const r = new FileReader(); r.onload = () => finish(String(r.result || "")); r.onerror = () => finish(""); r.readAsText(file);
      } else { finish(null); }
      // open the folder we dropped into
      if (rel.includes("/")) setFolders((f) => { const segs = (targetFolder ? targetFolder + "/" : "") + rel; const parts = segs.split("/").slice(0, -1); const acc = []; parts.forEach((_, i) => acc.push(parts.slice(0, i + 1).join("/"))); return [...new Set([...f, ...acc])]; });
      added++;
    });
    if (targetFolder) setOpenFolders((s) => new Set([...s, targetFolder]));
    flash(added + (added === 1 ? " file added — indexing…" : " files added — indexing…"));
  };

  const onRename = (node, rawName) => {
    const name = (rawName || "").trim();
    if (!name || name === node.name) return;
    if (node.type === "file") {
      const parts = node.path.split("/"); parts[parts.length - 1] = name; const np = parts.join("/");
      const newKind = extKind(name);
      setEntries((es) => es.map((e) => e.path === node.path ? { ...e, path: np, kind: TEXT_KINDS.includes(newKind) || !TEXT_KINDS.includes(e.kind) ? newKind : e.kind } : e));
      if (selectedPath === node.path) setSelectedPath(np);
    } else {
      const parts = node.path.split("/"); parts[parts.length - 1] = name; const np = parts.join("/");
      setEntries((es) => es.map((e) => e.path.startsWith(node.path + "/") ? { ...e, path: np + e.path.slice(node.path.length) } : e));
      setFolders((f) => f.map((p) => p === node.path ? np : p.startsWith(node.path + "/") ? np + p.slice(node.path.length) : p));
    }
  };

  const onDelete = (node) => {
    if (node.type === "file") {
      setEntries((es) => es.filter((e) => e.path !== node.path));
      if (selectedPath === node.path) setSelectedPath(null);
    } else {
      setEntries((es) => es.filter((e) => !(e.path === node.path || e.path.startsWith(node.path + "/"))));
      setFolders((f) => f.filter((p) => !(p === node.path || p.startsWith(node.path + "/"))));
    }
    flash("Deleted " + node.name);
  };

  const onDownload = (node) => {
    const e = entries.find((x) => x.path === node.path);
    const blob = new Blob([e && e.content != null ? e.content : ""], { type: "text/plain" });
    const url = URL.createObjectURL(blob); const a = document.createElement("a");
    a.href = url; a.download = node.name; a.click(); URL.revokeObjectURL(url);
  };

  const newFile = () => {
    const path = uniquePath("untitled.md");
    setEntries((es) => [...es, { path, kind: "md", content: "", chunks: 0, cited: 0, updated: TODAY, by: "You", size: "0 B", status: "draft" }]);
    setSelectedPath(path); setEditing(true); setDraft(""); setTimeout(() => setRenaming(path), 60);
  };
  const newFolder = () => {
    let name = "new-folder", i = 2; while (folders.includes(name) || tree.children.some((c) => c.type === "folder" && c.name === name)) { name = "new-folder-" + i++; }
    setFolders((f) => [...f, name]); setOpenFolders((s) => new Set([...s, name])); setTimeout(() => setRenaming(name), 60);
  };

  const startEdit = () => { setDraft(selected.content || ""); setEditing(true); };
  const saveEdit = () => {
    const path = selected.path;
    setEntries((es) => es.map((e) => e.path === path ? { ...e, content: draft, size: fmtSize(new Blob([draft]).size) } : e));
    if (!dEdit) setEditing(false);
    reindex(path); flash(dEdit ? "Saved — regenerating wiki…" : "Saved — re-indexing…");
  };
  const cancelEdit = () => setEditing(false);

  const openCtx = (e, node) => { e.preventDefault(); e.stopPropagation(); setCtx({ x: e.clientX, y: e.clientY, node }); };
  const ctxItems = (node) => node.type === "file" ? [
    { icon: "pencil", label: "Rename", onClick: () => setRenaming(node.path) },
    { icon: "download", label: "Download", onClick: () => onDownload(node) },
    { sep: true },
    { icon: "trash", label: "Delete", danger: true, onClick: () => onDelete(node) },
  ] : [
    { icon: "file_plus", label: "New file here", onClick: () => { const path = uniquePath(node.path + "/untitled.md"); setEntries((es) => [...es, { path, kind: "md", content: "", chunks: 0, cited: 0, updated: TODAY, by: "You", size: "0 B", status: "draft" }]); setOpenFolders((s) => new Set([...s, node.path])); setSelectedPath(path); setEditing(true); setDraft(""); setTimeout(() => setRenaming(path), 60); } },
    { icon: "pencil", label: "Rename", onClick: () => setRenaming(node.path) },
    { sep: true },
    { icon: "trash", label: "Delete folder", danger: true, onClick: () => onDelete(node) },
  ];

  const filtered = React.useMemo(() => {
    if (!query.trim()) return tree;
    const q = query.toLowerCase();
    const filt = (n) => { const kids = n.children.map((c) => c.type === "folder" ? filt(c) : (c.name.toLowerCase().includes(q) ? c : null)).filter(Boolean); return kids.length ? { ...n, children: kids } : null; };
    return filt(tree) || { ...tree, children: [] };
  }, [tree, query]);

  const isText = selected && TEXT_KINDS.includes(selected.kind);
  const dirty = selected && selected.content != null && draft !== selected.content;
  const path = selected ? selected.path : "";
  const crumbs = path ? path.split("/") : [];
  const lineCount = selected && selected.content != null ? (editing ? draft : selected.content).split("\n").length : 0;

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", borderTop: `1px solid ${RCA.paper3}`, overflow: "hidden", position: "relative" }}>
      <style>{`@keyframes rcaspin{to{transform:rotate(360deg)}}`}</style>
      {ctx && <CtxMenu x={ctx.x} y={ctx.y} items={ctxItems(ctx.node)} onClose={() => setCtx(null)}/>}
      {editable && <input ref={fileInputRef} type="file" multiple style={{ display: "none" }} onChange={(e) => { handleUpload(e.target.files, pendingFolder.current); pendingFolder.current = ""; e.target.value = ""; }}/>}

      {/* ---- TREE PANEL ---- */}
      <div style={{ width: 272, flexShrink: 0, borderRight: `1px solid ${RCA.paper3}`, background: RCA.paper, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative" }}
        onDragEnter={editable ? (e) => { e.preventDefault(); setDragActive(true); } : undefined}
        onDragOver={editable ? (e) => e.preventDefault() : undefined}
        onDragLeave={editable ? (e) => { if (e.currentTarget === e.target) setDragActive(false); } : undefined}
        onDrop={editable ? (e) => { e.preventDefault(); setDragActive(false); const t = dropTarget; setDropTarget(null); if (e.dataTransfer.files && e.dataTransfer.files.length) handleUpload(e.dataTransfer.files, t || ""); } : undefined}>
        <div style={{ height: 38, padding: "0 6px 0 14px", display: "flex", alignItems: "center", gap: 4, borderBottom: `1px solid ${RCA.paper3}` }}>
          <CapsLabel>Explorer</CapsLabel>
          <span className="mono" style={{ fontSize: 10, color: RCA.textPaperD2 }}>{countFiles(tree)}</span>
          <div style={{ flex: 1 }}/>
          {editable && <IconBtn name="file_plus" title="New file" onClick={newFile}/>}
          {editable && <IconBtn name="folder_plus" title="New folder" onClick={newFolder}/>}
          {editable && <IconBtn name="upload" title="Upload files" onClick={() => { pendingFolder.current = ""; fileInputRef.current && fileInputRef.current.click(); }}/>}
          <IconBtn name="collapse" title="Collapse all folders" onClick={collapseAll}/>
        </div>
        <div style={{ padding: "8px 10px", borderBottom: `1px solid ${RCA.paper3}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, height: 30, padding: "0 10px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 6 }}>
            <I name="search" size={12} color={RCA.textPaperD}/>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter files…" style={{ flex: 1, minWidth: 0, border: "none", outline: "none", background: "transparent", fontFamily: RCA.fBody, fontSize: 12, color: RCA.ink }}/>
            {query && <I name="x" size={12} color={RCA.textPaperD} style={{ cursor: "pointer" }} onClick={() => setQuery("")}/>}
          </div>
        </div>
        <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "6px 0" }}>
          {filtered.children.length ? filtered.children.map((c, i) => (
            <TreeNode key={c.path + i} node={c} depth={0} openFolders={query ? new Set(allFolders) : openFolders} toggle={toggle} selectedPath={selectedPath} onSelect={(d) => setSelectedPath(d.path)} editable={editable} renaming={renaming} setRenaming={setRenaming} onRename={onRename} onCtx={openCtx} dropTarget={dropTarget} setDropTarget={setDropTarget}/>
          )) : (
            <div style={{ padding: "24px 16px", textAlign: "center", fontSize: 12, color: RCA.textPaperD2 }}>{query ? `No files match “${query}”.` : "No files yet."}</div>
          )}
          {editable && !query && (
            <div onClick={() => { pendingFolder.current = ""; fileInputRef.current && fileInputRef.current.click(); }} style={{ margin: "10px 10px 4px", padding: "16px 12px", border: `1.5px dashed ${RCA.paper3}`, borderRadius: 8, display: "flex", flexDirection: "column", alignItems: "center", gap: 5, cursor: "pointer", textAlign: "center" }}>
              <I name="upload" size={16} color={RCA.textPaperD2}/>
              <div style={{ fontSize: 11.5, color: RCA.textPaperD }}>{dropLabel}</div>
              <div className="mono" style={{ fontSize: 10, color: RCA.textPaperD2 }}>{dropSub}</div>
            </div>
          )}
        </div>
        {dragActive && (
          <div style={{ position: "absolute", inset: 0, background: "rgba(240,80,46,0.06)", border: `2px dashed ${RCA.accent}`, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, pointerEvents: "none", zIndex: 5 }}>
            <I name="upload" size={26} color={RCA.accent}/>
            <div style={{ fontSize: 13, fontWeight: 600, color: RCA.accentH }}>Drop to add to this collection</div>
            <div className="mono" style={{ fontSize: 10.5, color: RCA.textPaperD }}>files or whole folders</div>
          </div>
        )}
      </div>

      {/* ---- EDITOR PANEL ---- */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", background: RCA.white, overflow: "hidden" }}>
        {/* tab strip */}
        <div style={{ height: 38, flexShrink: 0, display: "flex", alignItems: "stretch", background: RCA.paper2, borderBottom: `1px solid ${RCA.paper3}` }}>
          {selected && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 16px", background: RCA.white, borderRight: `1px solid ${RCA.paper3}`, borderTop: `2px solid ${editing ? RCA.accent : RCA.ink}`, marginTop: -1 }}>
              <I name={docIcon(selected.kind)} size={13} color={RCA.ink2}/>
              <span style={{ fontSize: 12.5, color: RCA.ink, fontWeight: 500 }}>{crumbs[crumbs.length - 1]}</span>
              {editing && dirty && <span style={{ width: 7, height: 7, borderRadius: "50%", background: RCA.accent, marginLeft: 2 }} title="Unsaved changes"/>}
            </div>
          )}
          <div style={{ flex: 1 }}/>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 12px" }}>
            {selected && editable && isText && !dEdit && !editing && (
              <Btn size="sm" variant="secondary" icon={<I name="pencil" size={12}/>} onClick={startEdit}>Edit</Btn>
            )}
            {selected && !dEdit && editing && (<>
              <Btn size="sm" variant="ghost" onClick={cancelEdit}>Cancel</Btn>
              <Btn size="sm" variant="primary" icon={<I name="save" size={12}/>} onClick={saveEdit}>Save</Btn>
            </>)}
            {selected && dEdit && isText && (
              dirty
                ? <Btn size="sm" variant="primary" icon={<I name="save" size={12}/>} onClick={saveEdit}>Save</Btn>
                : <span style={{ display: "inline-flex", alignItems: "center", gap: 5, height: 22, padding: "0 8px", borderRadius: 4, background: RCA.paper, border: `1px solid ${RCA.paper3}` }}>
                    <I name="check" size={11} color={RCA.ok}/>
                    <span className="mono" style={{ fontSize: 10.5, color: RCA.textPaperD }}>saved</span>
                  </span>
            )}
            {selected && (!editable || !isText) && !(dEdit && isText) && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5, height: 22, padding: "0 8px", borderRadius: 4, background: RCA.paper, border: `1px solid ${RCA.paper3}` }}>
                <I name="lock" size={11} color={RCA.textPaperD}/>
                <span className="mono" style={{ fontSize: 10.5, color: RCA.textPaperD }}>{!editable ? "auto-managed" : "preview"}</span>
              </span>
            )}
          </div>
        </div>

        {/* breadcrumb */}
        <div style={{ height: 32, flexShrink: 0, display: "flex", alignItems: "center", gap: 6, padding: "0 18px", borderBottom: `1px solid ${RCA.paper3}`, background: RCA.white }}>
          <I name="folder" size={12} color={RCA.textPaperD2}/>
          <span style={{ fontSize: 11.5, color: RCA.textPaperD2 }}>{collection.title}</span>
          {crumbs.map((c, i) => (
            <React.Fragment key={i}>
              <I name="chev_r" size={9} color={RCA.textPaperD2}/>
              <span style={{ fontSize: 11.5, fontFamily: i === crumbs.length - 1 ? RCA.fMono : RCA.fBody, color: i === crumbs.length - 1 ? RCA.ink : RCA.textPaperD2, fontWeight: i === crumbs.length - 1 ? 600 : 400 }}>{c}</span>
            </React.Fragment>
          ))}
        </div>

        {/* body */}
        {!selected ? (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, color: RCA.textPaperD2 }}>
            <I name="file" size={26} color={RCA.paper3}/>
            <div style={{ fontSize: 13 }}>Select a file from the tree{editable ? ", or drop files to add." : "."}</div>
          </div>
        ) : isText ? (
          <EditorPane entry={selected} editing={editing} draft={draft} setDraft={setDraft}/>
        ) : (
          <div className="scrollable" style={{ flex: 1, overflow: "auto", padding: "22px", background: RCA.paper2 }}>
            <DocPreviewBody doc={selected}/>
          </div>
        )}

        {/* sources footer (wiki) */}
        {selected && sourcesByPath && sourcesByPath[selected.path] && sourcesByPath[selected.path].length > 0 && (
          <div style={{ flexShrink: 0, borderTop: `1px solid ${RCA.paper3}`, background: RCA.paper, padding: "8px 16px", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="caps" style={{ fontSize: 10, color: RCA.textPaperD2 }}>Sources</span>
            {sourcesByPath[selected.path].map((s, i) => (
              <div key={i} onClick={() => onOpenSource && onOpenSource(s)} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 8px", background: RCA.white, border: `1px solid ${RCA.paper3}`, borderRadius: 5, cursor: "pointer" }}>
                <I name={s.endsWith(".xlsx") ? "table" : "file"} size={11} color={RCA.ink2}/>
                <span style={{ fontSize: 11, color: RCA.textPaper, fontFamily: RCA.fMono }}>{s}</span>
              </div>
            ))}
          </div>
        )}

        {/* status bar */}
        <div style={{ height: 28, flexShrink: 0, display: "flex", alignItems: "center", gap: 16, padding: "0 16px", borderTop: `1px solid ${RCA.paper3}`, background: editing ? RCA.accent : RCA.paper, fontFamily: RCA.fMono, fontSize: 11, color: editing ? RCA.white : RCA.textPaperD }}>
          {selected && <>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <I name={editing ? "pencil" : "lock"} size={11} color={editing ? RCA.white : RCA.textPaperD2}/>{editing ? "Editing" : (editable && isText && !dEdit ? "Read-only · click Edit" : "Read-only")}
            </span>
            <span>{KIND_LANG[selected.kind] || "File"}</span>
            {isText && <span>{lineCount} lines</span>}
            <span>UTF-8</span>
            <div style={{ flex: 1 }}/>
            {selected.status === "indexing" ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: editing ? RCA.white : RCA.accent }}><Spinner/>indexing…</span>
              : <span>indexed · {selected.chunks} chunks</span>}
            <span style={{ color: editing ? RCA.white : ((selected.cited ?? 0) > 0 ? RCA.accent : RCA.textPaperD2) }}>cited {selected.cited ?? 0}×</span>
            <span>{selected.updated}</span>
          </>}
        </div>
      </div>

      {/* toast */}
      {toast && (
        <div style={{ position: "absolute", bottom: 40, left: "50%", transform: "translateX(-50%)", zIndex: 60, display: "flex", alignItems: "center", gap: 9, padding: "10px 16px", background: RCA.ink, color: RCA.white, borderRadius: 8, fontSize: 12.5, boxShadow: "0 8px 28px rgba(22,24,29,0.22)" }}>
          <span style={{ width: 16, height: 16, borderRadius: 4, background: RCA.accent, display: "inline-flex", alignItems: "center", justifyContent: "center" }}><RCAMark size={11} color={RCA.white}/></span>
          {toast}
        </div>
      )}
    </div>
  );
}

function IconBtn({ name, title, onClick }) {
  return (
    <div title={title} onClick={onClick} style={{ width: 26, height: 26, borderRadius: 5, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: RCA.textPaperD }}
      onMouseEnter={(e) => (e.currentTarget.style.background = RCA.paper2)} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <I name={name} size={14}/>
    </div>
  );
}

Object.assign(window, { DocTreeView, CtxMenu, IconBtn, Spinner, buildTree, countFiles, docIcon, extKind, fmtSize });
