/**
 * VSCode-shaped workspace shell. Renders all chrome (top bar, activity
 * bar, sidebar, editor, bottom panel, status bar, agent panel) and
 * owns the file/tab state shared between them.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api";
import type { CloseStatus, FileInfo, Investigation } from "../../api/types";
import { formatInvestigationId, isOpen } from "../../api/types";
import { Icon, type IconName } from "../../components/Icon";
import { Popover, PopoverItem } from "../../components/Popover";
import { RcaMark } from "../../components/RcaMark";
import { ResizeDivider } from "../../components/ResizeDivider";
import { SeverityChip, StatusChip } from "../../components/StatusChip";
import { FileBufferProvider } from "../../hooks/fileBuffer";
import { AgentProvider, useAgent } from "../../hooks/useAgent";
import { useFileContent } from "../../hooks/useFileContent";
import { usePersistentDeque } from "../../hooks/usePersistentSet";
import { usePersistentNumber } from "../../hooks/usePersistentNumber";
import { emitRunAll } from "../../lib/editorEvents";
import { FileView } from "../../renderers/FileView";
import { AgentPanel } from "./AgentPanel";
import { CommandPalette } from "./CommandPalette";
import { basename, breadcrumbSegments, hasOutline, pickRenderer } from "./renderer";
import { TerminalPane } from "./TerminalPane";

type OpenTab = { path: string; modified: boolean };

export type ActivityMode = "evidence" | "search" | "history" | "reviewers";

const MODEL_OPTIONS = [
  "claude-opus-4",
  "claude-sonnet-4",
  "qwen3:14b",
  "gpt-4o",
];

export function InvestigationShell({
  investigation,
  files,
  onFilesChanged,
}: {
  investigation: Investigation;
  files: FileInfo[];
  onFilesChanged?: () => void;
}) {
  // The initial open tabs mirror the design's six view-files (those that
  // exist).
  const designViews = useMemo(
    () => [
      "/brief.md",
      "/drift.ipynb",
      "/pareto.ipynb",
      "/fishbone.canvas",
      "/5-why.md",
      "/report.v1.md",
    ],
    [],
  );
  const [openTabs, setOpenTabs] = useState<OpenTab[]>(() =>
    designViews
      .filter((p) => files.some((f) => f.path === p))
      .map((path) => ({ path, modified: false })),
  );
  const [activeTab, setActiveTab] = useState<string | null>(() => openTabs[0]?.path ?? null);
  const [activityMode, setActivityMode] = useState<ActivityMode>("evidence");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [model, setModel] = useState(MODEL_OPTIONS[0]!);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");

  // Resizable + collapsible panels (VSCode-style). Sizes persist; ⌘B/⌘J
  // toggle the sidebar / bottom panel.
  const [sidebarW, setSidebarW] = usePersistentNumber("rca:layout:sidebar", 260, 180, 560);
  const [agentW, setAgentW] = usePersistentNumber("rca:layout:agent", 380, 280, 680);
  const [bottomH, setBottomH] = usePersistentNumber("rca:layout:bottom", 200, 80, 600);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [bottomOpen, setBottomOpen] = useState(true);

  const recentFiles = usePersistentDeque(
    `rca:recent-files:${investigation.resource_id}`,
    10,
  );

  const openFile = useCallback(
    (path: string) => {
      setOpenTabs((prev) =>
        prev.some((t) => t.path === path) ? prev : [...prev, { path, modified: false }],
      );
      setActiveTab(path);
      recentFiles.push(path);
    },
    [recentFiles],
  );

  const closeTab = (path: string) => {
    setOpenTabs((prev) => {
      const remaining = prev.filter((t) => t.path !== path);
      setActiveTab((current) => {
        if (current !== path) return current;
        return remaining[remaining.length - 1]?.path ?? null;
      });
      return remaining;
    });
  };

  // Latest tab state for the keyboard handler (kept in a ref so the
  // listener binds once instead of re-subscribing on every tab change).
  const tabsRef = useRef({ openTabs, activeTab, closeTab, setActiveTab });
  tabsRef.current = { openTabs, activeTab, closeTab, setActiveTab };

  // Global keyboard: ⌘P palette · ⌘B sidebar · ⌘J bottom panel ·
  // ⌘W close active tab · ⌘1-9 jump to the Nth tab.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const k = e.key.toLowerCase();
      const { openTabs: tabs, activeTab: active, closeTab: close, setActiveTab: select } =
        tabsRef.current;
      if (k === "p") {
        e.preventDefault();
        setPaletteOpen(true);
      } else if (k === "b") {
        e.preventDefault();
        setSidebarOpen((v) => !v);
      } else if (k === "j") {
        e.preventDefault();
        setBottomOpen((v) => !v);
      } else if (k === "w") {
        if (active) {
          e.preventDefault();
          close(active);
        }
      } else if (k >= "1" && k <= "9") {
        const idx = Number.parseInt(k, 10) - 1;
        const target = tabs[idx];
        if (target) {
          e.preventDefault();
          select(target.path);
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const focusAgentComposer = () => {
    const composer = document.querySelector<HTMLTextAreaElement>(
      "[data-testid='agent-panel'] textarea",
    );
    composer?.focus();
  };

  return (
    <AgentProvider investigationId={investigation.resource_id}>
     <FileBufferProvider investigationId={investigation.resource_id}>
      <div
        data-testid="page-investigation"
        style={{
          height: "100vh",
          display: "flex",
          flexDirection: "column",
          background: "var(--paper)",
          overflow: "hidden",
        }}
      >
        <TopBar
          investigation={investigation}
          onCommandPalette={() => setPaletteOpen(true)}
          model={model}
          onModel={setModel}
        />
        <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
          <ActivityBar
            mode={activityMode}
            onMode={setActivityMode}
            onFocusAgent={focusAgentComposer}
            onSettings={() => setSettingsOpen(true)}
          />
          {sidebarOpen && (
            <>
              <div style={{ width: sidebarW, flexShrink: 0, display: "flex", minWidth: 0 }}>
                <ActivitySidebar
                  mode={activityMode}
                  investigation={investigation}
                  files={files}
                  activePath={activeTab}
                  openTabs={openTabs}
                  recentFiles={recentFiles.values}
                  onOpenFile={openFile}
                  onFilesChanged={onFilesChanged}
                />
              </div>
              <ResizeDivider
                orientation="vertical"
                ariaLabel="resize sidebar"
                onResize={(d) => setSidebarW(sidebarW + d)}
              />
            </>
          )}
          <EditorArea
            investigationId={investigation.resource_id}
            openTabs={openTabs}
            activeTab={activeTab}
            onSelectTab={setActiveTab}
            onCloseTab={closeTab}
            bottomHeight={bottomH}
            bottomOpen={bottomOpen}
            onResizeBottom={(d) => setBottomH(bottomH - d)}
            onToggleBottom={() => setBottomOpen((v) => !v)}
          />
          <ResizeDivider
            orientation="vertical"
            ariaLabel="resize agent panel"
            onResize={(d) => setAgentW(agentW - d)}
          />
          <AgentPanel investigationId={investigation.resource_id} width={agentW} />
        </div>

        <CommandPalette
          open={paletteOpen}
          files={files}
          onClose={() => setPaletteOpen(false)}
          onPick={openFile}
        />
        <SettingsModal
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          model={model}
          onModel={setModel}
          modelOptions={MODEL_OPTIONS}
          theme={theme}
          onTheme={setTheme}
        />
      </div>
     </FileBufferProvider>
    </AgentProvider>
  );
}

function SettingsModal({
  open,
  onClose,
  model,
  onModel,
  modelOptions,
  theme,
  onTheme,
}: {
  open: boolean;
  onClose: () => void;
  model: string;
  onModel: (m: string) => void;
  modelOptions: readonly string[];
  theme: "system" | "light" | "dark";
  onTheme: (t: "system" | "light" | "dark") => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 480,
          maxHeight: "80vh",
          overflow: "auto",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 12px 32px rgba(0,0,0,0.18)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--paper-3)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <Icon name="settings" size={14} />
          <strong style={{ fontSize: 13, flex: 1 }}>Settings</strong>
          <button
            type="button"
            aria-label="close settings"
            onClick={onClose}
            style={{ color: "var(--text-paper-d)" }}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        <SettingsSection label="Agent model">
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {modelOptions.map((m) => (
              <label
                key={m}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-btn)",
                  cursor: "pointer",
                  background: m === model ? "var(--accent-soft)" : "var(--white)",
                  fontSize: 12,
                }}
              >
                <input
                  type="radio"
                  name="model"
                  checked={m === model}
                  onChange={() => onModel(m)}
                />
                <span style={{ fontFamily: "var(--font-mono)" }}>{m}</span>
              </label>
            ))}
            <p style={{ margin: 0, fontSize: 11, color: "var(--text-paper-d)" }}>
              v1 default is the local Qwen via LiteLLM/Ollama; pick another
              model to route through your hosted credentials.
            </p>
          </div>
        </SettingsSection>

        <SettingsSection label="Theme">
          <div style={{ display: "flex", gap: 6 }}>
            {(["system", "light", "dark"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => onTheme(t)}
                style={{
                  padding: "6px 12px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-btn)",
                  fontSize: 12,
                  background: t === theme ? "var(--accent-soft)" : "var(--white)",
                  color: t === theme ? "var(--accent-h)" : "var(--text-paper)",
                  textTransform: "capitalize",
                }}
              >
                {t}
              </button>
            ))}
          </div>
          <p style={{ marginTop: 6, fontSize: 11, color: "var(--text-paper-d)" }}>
            Dark mode lands in v2 — v1 is fixed light.
          </p>
        </SettingsSection>

        <SettingsSection label="About">
          <dl
            style={{
              margin: 0,
              display: "grid",
              gridTemplateColumns: "max-content 1fr",
              rowGap: 4,
              columnGap: 12,
              fontSize: 12,
            }}
          >
            <dt style={{ color: "var(--text-paper-d)" }}>Product</dt>
            <dd style={{ margin: 0 }}>RCA 3.0</dd>
            <dt style={{ color: "var(--text-paper-d)" }}>Auth</dt>
            <dd style={{ margin: 0 }}>single-user demo (no sign-in)</dd>
            <dt style={{ color: "var(--text-paper-d)" }}>API</dt>
            <dd style={{ margin: 0 }}>
              <a href="/docs" target="_blank" rel="noreferrer">
                Swagger /docs
              </a>{" "}
              · <code style={{ fontSize: 11 }}>contract.md</code>
            </dd>
          </dl>
        </SettingsSection>
      </div>
    </div>
  );
}

function SettingsSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section
      style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div className="caps" style={{ fontSize: 11 }}>
        {label}
      </div>
      {children}
    </section>
  );
}

/* ------------------------------ Top bar ------------------------------ */

function TopBar({
  investigation,
  onCommandPalette,
  model,
  onModel,
}: {
  investigation: Investigation;
  onCommandPalette: () => void;
  model: string;
  onModel: (m: string) => void;
}) {
  const navigate = useNavigate();
  return (
    <div
      style={{
        height: 52,
        flexShrink: 0,
        background: "var(--white)",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "center",
        padding: "0 16px",
        gap: 12,
      }}
    >
      <button
        type="button"
        onClick={() => navigate("/")}
        style={{
          padding: "4px 8px",
          color: "var(--text-paper-d)",
          fontSize: "var(--text-body-sm)",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <Icon name="chev_l" size={14} /> All
      </button>
      <RcaMark size={22} />
      <span style={{ width: 1, height: 22, background: "var(--paper-3)" }} />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: "var(--text-body-sm)",
          color: "var(--text-paper-d)",
        }}
      >
        <span>acme</span>
        <Icon name="chev_r" size={12} color="var(--text-paper-d2)" />
        <span>{investigation.product || "SMT process"}</span>
        <Icon name="chev_r" size={12} color="var(--text-paper-d2)" />
        <span
          style={{
            color: "var(--text-paper)",
            fontWeight: 600,
            fontFamily: "var(--font-mono)",
          }}
        >
          {formatInvestigationId(investigation.resource_id)}
        </span>
        <SeverityChip level={investigation.severity} />
        <StatusChip status={investigation.status} />
      </div>
      <span style={{ flex: 1 }} />

      <button
        type="button"
        onClick={onCommandPalette}
        title="Go to file (⌘P)"
        style={{
          width: 320,
          height: 28,
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-btn)",
          background: "var(--paper)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 10px",
          color: "var(--text-paper-d)",
          fontSize: 12,
        }}
      >
        <Icon name="search" size={13} />
        <span>Go to file, symbol, command…</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>⌘P</span>
      </button>

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            style={{
              height: 28,
              padding: "0 10px",
              border: "1px solid var(--paper-3)",
              borderRadius: "var(--radius-btn)",
              fontSize: 12,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              background: open ? "var(--paper-2)" : "transparent",
            }}
          >
            {model} <Icon name="chev_d" size={12} />
          </button>
        )}
      >
        {(close) => (
          <div style={{ minWidth: 200 }}>
            {MODEL_OPTIONS.map((m) => (
              <PopoverItem
                key={m}
                selected={m === model}
                onClick={() => {
                  onModel(m);
                  close();
                }}
              >
                {m}
              </PopoverItem>
            ))}
          </div>
        )}
      </Popover>

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            title="Members"
            style={{
              ...iconBtn,
              display: "inline-flex",
              gap: 4,
              padding: "0 8px",
              width: "auto",
              background: open ? "var(--paper-2)" : "transparent",
            }}
          >
            <Icon name="users" size={15} />
            <span style={{ fontSize: 12 }}>{investigation.members.length + 1}</span>
          </button>
        )}
      >
        {() => (
          <div style={{ minWidth: 200, padding: "6px 0" }}>
            <MemberLine name={`${investigation.owner} (owner)`} />
            {investigation.members.map((m) => (
              <MemberLine key={m} name={m} />
            ))}
          </div>
        )}
      </Popover>

      <CloseInvestigationButton investigation={investigation} />

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            title="Notifications"
            style={{ ...iconBtn, background: open ? "var(--paper-2)" : "transparent" }}
          >
            <Icon name="bell" size={15} />
          </button>
        )}
      >
        {() => (
          <div style={{ minWidth: 240, padding: "6px 0" }}>
            <div className="caps" style={{ padding: "4px 10px" }}>Notifications</div>
            <div style={{ padding: "4px 10px", color: "var(--text-paper-d)", fontSize: 12 }}>
              No new notifications.
            </div>
          </div>
        )}
      </Popover>

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <button
            type="button"
            onClick={onClick}
            title={investigation.owner}
            style={{
              width: 24,
              height: 24,
              borderRadius: "50%",
              background: open ? "var(--paper-3)" : "var(--paper-2)",
              border: "1px solid var(--paper-3)",
              fontSize: 11,
              fontWeight: 600,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {investigation.owner.slice(0, 2).toUpperCase()}
          </button>
        )}
      >
        {() => (
          <div style={{ minWidth: 160 }}>
            <div style={{ padding: "8px 10px", fontWeight: 600, fontSize: 12 }}>
              {investigation.owner}
            </div>
          </div>
        )}
      </Popover>
    </div>
  );
}

function CloseInvestigationButton({
  investigation,
}: {
  investigation: Investigation;
}) {
  const navigate = useNavigate();
  const [pending, setPending] = useState<CloseStatus | null>(null);
  const alreadyClosed = !isOpen(investigation.status);

  const close = async (status: CloseStatus, dismiss: () => void) => {
    if (pending) return;
    setPending(status);
    try {
      await api.closeInvestigation(investigation.resource_id, status);
      dismiss();
      navigate("/");
    } catch (e) {
      console.error("closeInvestigation failed", e);
      alert(`Close failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPending(null);
    }
  };

  return (
    <Popover
      align="end"
      trigger={({ onClick, open }) => (
        <button
          type="button"
          onClick={onClick}
          title={alreadyClosed ? `Already ${investigation.status}` : "Close investigation"}
          disabled={alreadyClosed}
          style={{
            height: 28,
            padding: "0 10px",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            fontSize: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            background: open ? "var(--paper-2)" : "transparent",
            color: alreadyClosed ? "var(--text-paper-d2)" : "var(--text-paper-d)",
            cursor: alreadyClosed ? "not-allowed" : "pointer",
          }}
        >
          <Icon name="check" size={12} /> Close
        </button>
      )}
    >
      {(dismiss) => (
        <div style={{ minWidth: 200 }}>
          <div className="caps" style={{ padding: "6px 10px" }}>Close as…</div>
          <PopoverItem
            onClick={() => {
              void close("resolved", dismiss);
            }}
          >
            {pending === "resolved" ? "Closing…" : "Resolved"}
          </PopoverItem>
          <PopoverItem
            onClick={() => {
              void close("abandoned", dismiss);
            }}
          >
            {pending === "abandoned" ? "Closing…" : "Abandoned"}
          </PopoverItem>
        </div>
      )}
    </Popover>
  );
}

function MemberLine({ name }: { name: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 10px",
        fontSize: 12,
      }}
    >
      <span
        style={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          background: "var(--paper-2)",
          border: "1px solid var(--paper-3)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 10,
          fontWeight: 600,
        }}
      >
        {name.slice(0, 2).toUpperCase()}
      </span>
      {name}
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  width: 32,
  height: 28,
  borderRadius: "var(--radius-btn)",
  border: "1px solid transparent",
  color: "var(--text-paper-d)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
};

/* ----------------------------- Activity bar ----------------------------- */

function ActivityBar({
  mode,
  onMode,
  onFocusAgent,
  onSettings,
}: {
  mode: ActivityMode;
  onMode: (m: ActivityMode) => void;
  onFocusAgent: () => void;
  onSettings: () => void;
}) {
  const items: {
    name: IconName;
    label: string;
    onClick: () => void;
    active: boolean;
  }[] = [
    { name: "folder", label: "Evidence", onClick: () => onMode("evidence"), active: mode === "evidence" },
    { name: "search", label: "Search files", onClick: () => onMode("search"), active: mode === "search" },
    { name: "sparkle", label: "Focus agent", onClick: onFocusAgent, active: false },
    { name: "clock", label: "History", onClick: () => onMode("history"), active: mode === "history" },
    { name: "users", label: "Reviewers", onClick: () => onMode("reviewers"), active: mode === "reviewers" },
  ];
  return (
    <div
      style={{
        width: 50,
        flexShrink: 0,
        background: "var(--paper)",
        borderRight: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "8px 0",
      }}
    >
      {items.map((it) => (
        <button
          key={it.label}
          type="button"
          title={it.label}
          onClick={it.onClick}
          style={{
            width: 50,
            height: 44,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            color: it.active ? "var(--accent)" : "var(--text-paper-d)",
            borderLeft: it.active ? "2px solid var(--accent)" : "2px solid transparent",
            background: it.active ? "var(--accent-soft)" : "transparent",
          }}
        >
          <Icon name={it.name} size={18} />
        </button>
      ))}
      <span style={{ flex: 1 }} />
      <button
        type="button"
        title="Settings"
        onClick={onSettings}
        style={{ width: 50, height: 44, color: "var(--text-paper-d)" }}
      >
        <Icon name="settings" size={18} />
      </button>
    </div>
  );
}

/* ----------------------------- Sidebar wrapper ----------------------------- */

function ActivitySidebar(props: {
  mode: ActivityMode;
  investigation: Investigation;
  files: FileInfo[];
  activePath: string | null;
  openTabs: OpenTab[];
  recentFiles: string[];
  onOpenFile: (path: string) => void;
  onFilesChanged?: () => void;
}) {
  switch (props.mode) {
    case "evidence":
      return <EvidenceSidebar {...props} />;
    case "search":
      return <SearchSidebar files={props.files} onOpenFile={props.onOpenFile} />;
    case "history":
      return <HistorySidebar files={props.files} recentFiles={props.recentFiles} onOpenFile={props.onOpenFile} />;
    case "reviewers":
      return <ReviewersSidebar investigation={props.investigation} />;
  }
}

/* ----------------------------- Evidence sidebar ----------------------------- */

function EvidenceSidebar({
  investigation,
  files,
  activePath,
  openTabs,
  onOpenFile,
  onFilesChanged,
}: {
  investigation: Investigation;
  files: FileInfo[];
  activePath: string | null;
  openTabs: OpenTab[];
  onOpenFile: (path: string) => void;
  onFilesChanged?: () => void;
}) {
  // Group by top-level directory; root-level files go under "(root)"
  const byDir = new Map<string, FileInfo[]>();
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    const head = parts.length <= 1 ? "(root)" : parts[0]!;
    if (!byDir.has(head)) byDir.set(head, []);
    byDir.get(head)!.push(f);
  }

  return (
    <SidebarFrame
      investigation={investigation}
      header={
        <>
          <span className="caps">Evidence</span>
          <SidebarUploadButton
            investigationId={investigation.resource_id}
            existingPaths={files.map((f) => f.path)}
            onUploaded={(path) => {
              onFilesChanged?.();
              onOpenFile(path);
            }}
          />
        </>
      }
    >
      {openTabs.length > 0 && (
        <Section title="Open">
          {openTabs.map((t) => (
            <TreeRow
              key={t.path}
              label={basename(t.path)}
              path={t.path}
              active={t.path === activePath}
              onOpen={onOpenFile}
            />
          ))}
        </Section>
      )}

      <Section title="Investigation files">
        {[...byDir.entries()].map(([dir, items]) => (
          <div key={dir}>
            {dir !== "(root)" && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 14px",
                  color: "var(--text-paper-d)",
                  fontSize: 12,
                }}
              >
                <Icon name="chev_d" size={12} />
                <Icon name="folder" size={13} />
                <span>{dir}</span>
              </div>
            )}
            {items.map((f) => (
              <TreeRow
                key={f.path}
                label={basename(f.path)}
                path={f.path}
                indent={dir === "(root)" ? 14 : 28}
                active={f.path === activePath}
                onOpen={onOpenFile}
              />
            ))}
          </div>
        ))}
      </Section>

      <OutlineSection activePath={activePath} investigationId={investigation.resource_id} />
    </SidebarFrame>
  );
}

function SidebarUploadButton({
  investigationId,
  existingPaths,
  onUploaded,
}: {
  investigationId: string;
  existingPaths: string[];
  onUploaded: (path: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    // 8 MB cap on the sidebar uploader — larger than the agent attach
    // (256 KB) since this lane handles evidence files, including CSV
    // exports and ipynb backups.
    if (file.size > 8 * 1024 * 1024) {
      alert(`File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). v1 cap is 8 MB.`);
      return;
    }
    const target = `/uploads/${file.name}`;
    if (existingPaths.includes(target)) {
      if (!confirm(`${target} already exists. Overwrite?`)) return;
    }
    setBusy(true);
    try {
      await api.writeFile(investigationId, target, file);
      onUploaded(target);
    } catch (err) {
      console.error("upload failed", err);
      alert(`Upload failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) void upload(f);
        }}
        style={{ display: "none" }}
      />
      <button
        type="button"
        title={busy ? "Uploading…" : "Upload evidence file"}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        style={{
          color: busy ? "var(--text-paper-d2)" : "var(--text-paper-d)",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <Icon name={busy ? "upload" : "plus"} size={14} />
      </button>
    </>
  );
}

function OutlineSection({
  activePath,
  investigationId,
}: {
  activePath: string | null;
  investigationId: string;
}) {
  // Outline is meaningful for any markdown-bodied file — that's the
  // markdown renderer AND the report renderer (/report.v*.md).
  const renderable = activePath != null && hasOutline(activePath);
  const content = useFileContent(
    investigationId,
    renderable ? activePath : null,
  );

  if (!renderable) {
    return (
      <Section title="Outline">
        <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
          (open a markdown or report file to see headings)
        </div>
      </Section>
    );
  }
  if (content.kind !== "ready" || content.content.kind !== "text") {
    return (
      <Section title="Outline">
        <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
          …
        </div>
      </Section>
    );
  }
  const headings = extractHeadings(content.content.text);
  return (
    <Section title="Outline">
      {headings.length === 0 && (
        <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
          No headings in this file.
        </div>
      )}
      {headings.map((h, i) => (
        <a
          key={i}
          href={`#${slugify(h.text)}`}
          style={{
            display: "block",
            padding: `4px 14px 4px ${14 + (h.level - 1) * 10}px`,
            fontSize: 12,
            color: "var(--text-paper)",
            textDecoration: "none",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLAnchorElement).style.background = "var(--paper-2)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLAnchorElement).style.background = "transparent";
          }}
        >
          {h.text}
        </a>
      ))}
    </Section>
  );
}

export function extractHeadings(md: string): { level: number; text: string }[] {
  const out: { level: number; text: string }[] = [];
  for (const line of md.split("\n")) {
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (m && m[1] && m[2]) {
      out.push({ level: m[1].length, text: m[2] });
    }
  }
  return out;
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/* ----------------------------- Search sidebar ----------------------------- */

function SearchSidebar({
  files,
  onOpenFile,
}: {
  files: FileInfo[];
  onOpenFile: (p: string) => void;
}) {
  const [q, setQ] = useState("");
  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return files.slice(0, 50);
    return files.filter((f) => f.path.toLowerCase().includes(needle));
  }, [q, files]);
  return (
    <aside style={sidebarStyle}>
      <div style={sidebarHeader}>
        <span className="caps">Search files</span>
      </div>
      <div style={{ padding: 10 }}>
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filename contains…"
          style={{
            width: "100%",
            height: 28,
            padding: "0 8px",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            outline: "none",
            fontSize: 12,
          }}
        />
      </div>
      <div className="scrollable" style={{ flex: 1, overflowY: "auto" }}>
        {matches.length === 0 && (
          <div style={{ padding: "8px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
            No matches.
          </div>
        )}
        {matches.map((f) => (
          <TreeRow
            key={f.path}
            label={basename(f.path)}
            path={f.path}
            active={false}
            onOpen={onOpenFile}
          />
        ))}
      </div>
    </aside>
  );
}

/* ----------------------------- History sidebar ----------------------------- */

function HistorySidebar({
  files,
  recentFiles,
  onOpenFile,
}: {
  files: FileInfo[];
  recentFiles: string[];
  onOpenFile: (p: string) => void;
}) {
  // Filter recentFiles to those still present in the file listing.
  const items = recentFiles.filter((p) => files.some((f) => f.path === p));
  return (
    <aside style={sidebarStyle}>
      <div style={sidebarHeader}>
        <span className="caps">Recently opened</span>
      </div>
      <div className="scrollable" style={{ flex: 1, overflowY: "auto" }}>
        {items.length === 0 && (
          <div style={{ padding: "8px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
            History is empty.
          </div>
        )}
        {items.map((p) => (
          <TreeRow
            key={p}
            label={basename(p)}
            path={p}
            active={false}
            onOpen={onOpenFile}
          />
        ))}
      </div>
    </aside>
  );
}

/* ----------------------------- Reviewers sidebar ----------------------------- */

function ReviewersSidebar({ investigation }: { investigation: Investigation }) {
  return (
    <aside style={sidebarStyle}>
      <div style={sidebarHeader}>
        <span className="caps">Reviewers</span>
      </div>
      <div style={{ padding: 12, fontSize: 12, color: "var(--text-paper)" }}>
        <div style={{ marginBottom: 4 }}>
          <strong>{investigation.owner}</strong>{" "}
          <span style={{ color: "var(--text-paper-d)" }}>(owner)</span>
        </div>
        {investigation.members.map((m) => (
          <div key={m}>{m}</div>
        ))}
        {investigation.members.length === 0 && (
          <div style={{ color: "var(--text-paper-d)" }}>No additional members.</div>
        )}
      </div>
    </aside>
  );
}

/* ----------------------------- Shared sidebar frame ----------------------------- */

const sidebarStyle: React.CSSProperties = {
  // Fills the resizable wrapper in InvestigationShell (width lives there).
  width: "100%",
  flex: 1,
  minWidth: 0,
  background: "var(--paper)",
  borderRight: "1px solid var(--paper-3)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const sidebarHeader: React.CSSProperties = {
  padding: "12px 14px",
  borderBottom: "1px solid var(--paper-3)",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

function SidebarFrame({
  investigation,
  header,
  children,
}: {
  investigation: Investigation;
  header: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <aside className="scrollable" style={{ ...sidebarStyle, overflowY: "auto" }}>
      <div style={sidebarHeader}>{header}</div>
      {children}
      <div style={{ flex: 1 }} />
      <footer
        style={{
          padding: "12px 14px",
          borderTop: "1px solid var(--paper-3)",
          background: "var(--paper-2)",
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          rowGap: 4,
          columnGap: 8,
          fontSize: 11,
        }}
      >
        <FootMeta label="Severity"><SeverityChip level={investigation.severity} /></FootMeta>
        <FootMeta label="Status"><StatusChip status={investigation.status} /></FootMeta>
        <FootMeta label="Owner">{investigation.owner}</FootMeta>
        <FootMeta label="Product">{investigation.product || "—"}</FootMeta>
        <FootMeta label="Opened">
          {new Date(investigation.created_time).toLocaleDateString()}
        </FootMeta>
      </footer>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: "8px 0", borderBottom: "1px solid var(--paper-3)" }}>
      <div className="caps" style={{ padding: "0 14px 4px" }}>{title}</div>
      {children}
    </div>
  );
}

function TreeRow({
  label,
  path,
  active,
  indent = 14,
  onOpen,
}: {
  label: string;
  path: string;
  active: boolean;
  indent?: number;
  onOpen: (p: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(path)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: "100%",
        padding: `4px 14px 4px ${indent}px`,
        textAlign: "left",
        background: active ? "var(--accent-soft)" : "transparent",
        borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
        color: active ? "var(--accent-h)" : "var(--text-paper)",
        fontSize: 12,
      }}
    >
      <Icon name="file" size={13} color="var(--text-paper-d)" />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {label}
      </span>
    </button>
  );
}

function FootMeta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span style={{ color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)", fontSize: 10 }}>
        {label}
      </span>
      <span style={{ color: "var(--text-paper)" }}>{children}</span>
    </>
  );
}

/* ----------------------------- Editor area ----------------------------- */

function EditorArea({
  investigationId,
  openTabs,
  activeTab,
  onSelectTab,
  onCloseTab,
  bottomHeight,
  bottomOpen,
  onResizeBottom,
  onToggleBottom,
}: {
  investigationId: string;
  openTabs: OpenTab[];
  activeTab: string | null;
  onSelectTab: (p: string) => void;
  onCloseTab: (p: string) => void;
  bottomHeight: number;
  bottomOpen: boolean;
  onResizeBottom: (deltaPx: number) => void;
  onToggleBottom: () => void;
}) {
  const [bottomTab, setBottomTab] = useState<"problems" | "output" | "terminal" | "agent_log" | "run_history">("agent_log");
  const [layoutMode, setLayoutMode] = useState<"single" | "split">("single");
  // Right column's active tab. When null we mirror activeTab; once the
  // user explicitly picks a different tab on the right side, this holds
  // their pick so the two columns can show different files.
  const [rightTab, setRightTab] = useState<string | null>(null);

  // Pick a sensible default for the right column the first time the
  // user enters split mode: prefer the next open tab so the user
  // immediately sees two different files side by side.
  useEffect(() => {
    if (layoutMode !== "split" || rightTab) return;
    const idx = openTabs.findIndex((t) => t.path === activeTab);
    const next = openTabs[idx + 1] ?? openTabs.find((t) => t.path !== activeTab);
    if (next) setRightTab(next.path);
  }, [layoutMode, rightTab, openTabs, activeTab]);

  // If the right column's tab was closed, drop the pin so it falls back
  // to mirroring the left.
  useEffect(() => {
    if (rightTab && !openTabs.some((t) => t.path === rightTab)) {
      setRightTab(null);
    }
  }, [openTabs, rightTab]);

  const effectiveRight = rightTab ?? activeTab;

  return (
    <section style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <TabStrip
        tabs={openTabs}
        active={activeTab}
        onSelect={onSelectTab}
        onClose={onCloseTab}
        layoutMode={layoutMode}
        onLayoutMode={(m) => {
          setLayoutMode(m);
          if (m === "single") setRightTab(null);
        }}
      />
      <Breadcrumb activeTab={activeTab} />
      {layoutMode === "split" ? (
        <div style={{ flex: 1, display: "flex", minHeight: 0, background: "var(--white)" }}>
          <SplitPane
            investigationId={investigationId}
            path={activeTab}
            placeholder="Pick a tab → it opens here."
          />
          <div
            style={{ width: 1, background: "var(--paper-3)", flexShrink: 0 }}
            aria-hidden
          />
          <SplitPane
            investigationId={investigationId}
            path={effectiveRight}
            placeholder="Pick from the dropdown above."
            header={
              <RightPaneTabPicker
                openTabs={openTabs}
                current={effectiveRight}
                onPick={setRightTab}
              />
            }
          />
        </div>
      ) : (
        <div
          className="scrollable"
          style={{ flex: 1, overflow: "auto", padding: 20, background: "var(--white)" }}
        >
          {activeTab ? (
            <FileView investigationId={investigationId} path={activeTab} />
          ) : (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
                color: "var(--text-paper-d)",
              }}
            >
              Open a file from the sidebar to view it here.
            </div>
          )}
        </div>
      )}

      {bottomOpen && (
        <ResizeDivider
          orientation="horizontal"
          ariaLabel="resize bottom panel"
          onResize={onResizeBottom}
        />
      )}
      <BottomPanel
        tab={bottomTab}
        onTab={setBottomTab}
        investigationId={investigationId}
        height={bottomHeight}
        open={bottomOpen}
        onToggle={onToggleBottom}
      />
      <StatusBar activeTab={activeTab} investigationId={investigationId} />
    </section>
  );
}

function SplitPane({
  investigationId,
  path,
  placeholder,
  header,
}: {
  investigationId: string;
  path: string | null;
  placeholder: string;
  header?: React.ReactNode;
}) {
  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {header && (
        <div
          style={{
            padding: "4px 12px",
            borderBottom: "1px solid var(--paper-3)",
            background: "var(--paper)",
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: "var(--text-paper-d)",
            minHeight: 28,
          }}
        >
          {header}
        </div>
      )}
      <div
        className="scrollable"
        style={{
          flex: 1,
          overflow: "auto",
          padding: 20,
        }}
      >
        {path ? (
          <FileView investigationId={investigationId} path={path} />
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "var(--text-paper-d2)",
              fontSize: 12,
            }}
          >
            {placeholder}
          </div>
        )}
      </div>
    </div>
  );
}

function RightPaneTabPicker({
  openTabs,
  current,
  onPick,
}: {
  openTabs: OpenTab[];
  current: string | null;
  onPick: (p: string) => void;
}) {
  return (
    <>
      <Icon name="split" size={11} color="var(--accent)" />
      <span style={{ fontFamily: "var(--font-mono)" }}>right pane:</span>
      <select
        value={current ?? ""}
        onChange={(e) => {
          if (e.target.value) onPick(e.target.value);
        }}
        style={{
          fontSize: 11,
          fontFamily: "var(--font-mono)",
          padding: "2px 4px",
          border: "1px solid var(--paper-3)",
          borderRadius: 3,
          background: "var(--white)",
          color: "var(--text-paper)",
        }}
      >
        {openTabs.length === 0 && <option value="">(no tabs)</option>}
        {openTabs.map((t) => (
          <option key={t.path} value={t.path}>
            {basename(t.path)}
          </option>
        ))}
      </select>
    </>
  );
}

function Breadcrumb({ activeTab }: { activeTab: string | null }) {
  const segments = activeTab ? breadcrumbSegments(activeTab) : [];
  return (
    <div
      style={{
        height: 28,
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "center",
        padding: "0 16px",
        background: "var(--white)",
        fontSize: 12,
        color: "var(--text-paper-d)",
        gap: 6,
      }}
    >
      <Icon name="folder" size={12} />
      {segments.map((s, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {s}
          <Icon name="chev_r" size={10} color="var(--text-paper-d2)" />
        </span>
      ))}
      <span style={{ color: "var(--text-paper)" }}>
        {activeTab ? basename(activeTab) : "no file"}
      </span>
    </div>
  );
}

function TabStrip({
  tabs,
  active,
  onSelect,
  onClose,
  layoutMode,
  onLayoutMode,
}: {
  tabs: OpenTab[];
  active: string | null;
  onSelect: (p: string) => void;
  onClose: (p: string) => void;
  layoutMode: "single" | "split";
  onLayoutMode: (m: "single" | "split") => void;
}) {
  const activeIsNotebook = active != null && pickRenderer(active) === "notebook";

  return (
    <div
      style={{
        height: 38,
        background: "var(--paper)",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "stretch",
        flexShrink: 0,
      }}
    >
      <div className="scrollable" style={{ display: "flex", flex: 1, overflowX: "auto" }}>
        {tabs.map((t) => {
          const isActive = t.path === active;
          return (
            <div
              key={t.path}
              role="tab"
              aria-selected={isActive}
              onClick={() => onSelect(t.path)}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "0 12px",
                borderTop: isActive ? "2px solid var(--accent)" : "2px solid transparent",
                background: isActive ? "var(--white)" : "transparent",
                borderRight: "1px solid var(--paper-3)",
                color: isActive ? "var(--text-paper)" : "var(--text-paper-d)",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              <Icon name="file" size={12} />
              <span style={{ fontSize: 12 }}>{basename(t.path)}</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(t.path);
                }}
                aria-label={`close ${basename(t.path)}`}
                style={{
                  width: 16,
                  height: 16,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--text-paper-d2)",
                  borderRadius: 3,
                }}
              >
                {t.modified ? (
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--warn)" }} />
                ) : (
                  <Icon name="x" size={10} />
                )}
              </button>
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "0 8px" }}>
        <button
          type="button"
          title={layoutMode === "split" ? "Single column" : "Split view"}
          onClick={() => onLayoutMode(layoutMode === "split" ? "single" : "split")}
          style={{
            ...iconBtn,
            color:
              layoutMode === "split" ? "var(--accent)" : "var(--text-paper-d)",
            background:
              layoutMode === "split" ? "var(--accent-soft)" : "transparent",
          }}
        >
          <Icon name="split" size={14} />
        </button>
        <button
          type="button"
          title={activeIsNotebook ? "Run all cells" : "Open a notebook to run cells"}
          disabled={!activeIsNotebook}
          onClick={() => active && emitRunAll(active)}
          style={{
            ...iconBtn,
            padding: "0 8px",
            width: "auto",
            color: activeIsNotebook ? "var(--accent)" : "var(--text-paper-d2)",
            fontSize: 12,
            display: "inline-flex",
            gap: 4,
            cursor: activeIsNotebook ? "pointer" : "not-allowed",
          }}
        >
          <Icon
            name="play"
            size={12}
            color={activeIsNotebook ? "var(--accent)" : "var(--text-paper-d2)"}
          />{" "}
          Run all
        </button>
      </div>
    </div>
  );
}

function BottomPanel({
  tab,
  onTab,
  investigationId,
  height,
  open,
  onToggle,
}: {
  tab: "problems" | "output" | "terminal" | "agent_log" | "run_history";
  onTab: (t: "problems" | "output" | "terminal" | "agent_log" | "run_history") => void;
  investigationId: string;
  height: number;
  open: boolean;
  onToggle: () => void;
}) {
  const tabs = [
    { key: "problems" as const, label: "Problems" },
    { key: "output" as const, label: "Output" },
    { key: "terminal" as const, label: "Terminal" },
    { key: "agent_log" as const, label: "Agent log" },
    { key: "run_history" as const, label: "Run history" },
  ];

  return (
    <div
      style={{
        height: open ? height : 32,
        flexShrink: 0,
        borderTop: "1px solid var(--paper-3)",
        background: "var(--white)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          height: 32,
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "0 14px",
          borderBottom: "1px solid var(--paper-3)",
        }}
      >
        {tabs.map((t) => {
          const active = t.key === tab;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onTab(t.key)}
              style={{
                padding: "0 10px",
                height: 32,
                borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                color: active ? "var(--text-paper)" : "var(--text-paper-d)",
                fontSize: 12,
                fontWeight: active ? 600 : 500,
                marginBottom: -1,
              }}
            >
              {t.label}
            </button>
          );
        })}
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={onToggle}
          title={open ? "Collapse panel (⌘J)" : "Expand panel (⌘J)"}
          aria-label="toggle bottom panel"
          style={{ color: "var(--text-paper-d)", padding: 4 }}
        >
          <Icon name={open ? "chev_d" : "chev_r"} size={14} />
        </button>
      </div>
      {open &&
        (tab === "terminal" ? (
          <div
            style={{
              flex: 1,
              minHeight: 0,
              padding: "8px 14px",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <TerminalPane investigationId={investigationId} />
          </div>
        ) : (
          <div
            className="scrollable"
            style={{
              flex: 1,
              overflow: "auto",
              padding: "8px 14px",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "var(--text-paper)",
            }}
          >
            <PanelBody tab={tab} />
          </div>
        ))}
    </div>
  );
}

function PanelBody({
  tab,
}: {
  tab: "problems" | "output" | "terminal" | "agent_log" | "run_history";
}) {
  const { log } = useAgent();

  if (tab === "problems") {
    const banners = log.entries.filter((e) => e.kind === "banner");
    const toolErrors = log.entries.filter(
      (e) => e.kind === "tool_call" && e.call.parseError,
    );
    if (!log.error && banners.length === 0 && toolErrors.length === 0) {
      return <div style={{ color: "var(--text-paper-d)" }}>No problems detected.</div>;
    }
    return (
      <>
        {log.error && <LogLine ts="now" kind="warn" text={`stream error: ${log.error}`} />}
        {banners.map((b, i) =>
          b.kind === "banner" ? <LogLine key={`b-${i}`} ts="—" kind="warn" text={b.text} /> : null,
        )}
        {toolErrors.map((e, i) =>
          e.kind === "tool_call" && e.call.parseError ? (
            <LogLine
              key={`e-${i}`}
              ts="—"
              kind="warn"
              text={`${e.call.name}: parse-error → ${e.call.parseError}`}
            />
          ) : null,
        )}
      </>
    );
  }

  if (tab === "output") {
    const calls = log.entries.filter((e) => e.kind === "tool_call");
    if (calls.length === 0) {
      return <div style={{ color: "var(--text-paper-d)" }}>No tool output yet.</div>;
    }
    return (
      <>
        {calls.map((e, i) =>
          e.kind === "tool_call" && e.call.output !== undefined ? (
            <div key={i} style={{ marginBottom: 4 }}>
              <div style={{ color: "var(--accent)" }}>
                → {e.call.name}
                {e.call.status === "running" && (
                  <span style={{ color: "var(--text-paper-d)" }}> (running)</span>
                )}
              </div>
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  color: "var(--text-paper)",
                  fontSize: 12,
                }}
              >
                {e.call.output}
              </pre>
            </div>
          ) : null,
        )}
      </>
    );
  }

  // 'terminal' is handled at the BottomPanel level (it needs to claim
  // the full panel height) — see TerminalPane.

  if (tab === "run_history") {
    const calls = log.entries.filter(
      (e) => e.kind === "tool_call",
    );
    if (calls.length === 0) {
      return <div style={{ color: "var(--text-paper-d)" }}>No tool runs yet.</div>;
    }
    return (
      <>
        {calls.map((e, i) =>
          e.kind === "tool_call" ? (
            <LogLine
              key={i}
              ts={e.call.status === "running" ? "•" : "✓"}
              kind={e.call.status === "running" ? "accent" : "muted"}
              text={`${e.call.name}(${Object.keys(e.call.args).join(", ")})`}
            />
          ) : null,
        )}
      </>
    );
  }

  // agent_log — show a derived line per entry
  type Line = { k: "info" | "accent" | "warn" | "muted"; t: string; key: number };
  const lines: Line[] = log.entries.flatMap((e, i): Line[] => {
    if (e.kind === "banner") return [{ k: "warn", t: e.text, key: i }];
    if (e.kind === "tool_call") {
      return [
        {
          k: e.call.status === "running" ? "accent" : "muted",
          t: `${e.call.status === "running" ? "tool_start" : "tool_end"} ${e.call.name}`,
          key: i,
        },
      ];
    }
    return [
      {
        k: e.message.role === "user" ? "info" : "muted",
        t: `${e.message.role}: ${e.message.content.slice(0, 80)}`,
        key: i,
      },
    ];
  });
  if (lines.length === 0 && !log.streaming) {
    return <div style={{ color: "var(--text-paper-d)" }}>Idle.</div>;
  }
  return (
    <>
      {lines.map((l) => (
        <LogLine key={l.key} ts="" kind={l.k} text={l.t} />
      ))}
      {log.streaming && <LogLine ts="" kind="accent" text="…streaming" />}
    </>
  );
}

function LogLine({
  ts,
  kind,
  text,
}: {
  ts: string;
  kind: "info" | "accent" | "warn" | "muted";
  text: string;
}) {
  const color =
    kind === "info"
      ? "var(--info)"
      : kind === "accent"
        ? "var(--accent)"
        : kind === "warn"
          ? "var(--warn)"
          : "var(--text-paper-d)";
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <span style={{ width: 64, color: "var(--text-paper-d2)" }}>{ts}</span>
      <span style={{ width: 60, color }}>{kind}</span>
      <span>{text}</span>
    </div>
  );
}

function StatusBar({
  activeTab,
  investigationId,
}: {
  activeTab: string | null;
  investigationId: string;
}) {
  const kind = activeTab ? pickRenderer(activeTab) : null;
  const isNotebook = kind === "notebook" && activeTab != null;
  return (
    <div
      style={{
        height: 28,
        flexShrink: 0,
        background: "var(--ink)",
        color: "var(--text-dark)",
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 12px",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <Icon name="branch" size={12} color="var(--text-dark)" /> main
      </span>
      <span>↑ 0 ↓ 0</span>
      <span style={{ flex: 1 }} />
      <span>{kind ? `lang: ${kind}` : ""}</span>
      {isNotebook && (
        <KernelStatusPill
          investigationId={investigationId}
          notebookPath={activeTab}
        />
      )}
      <span>UTF-8</span>
      <span>default-user</span>
    </div>
  );
}

function KernelStatusPill({
  investigationId,
  notebookPath,
}: {
  investigationId: string;
  notebookPath: string;
}) {
  const [state, setState] = useState<"idle" | "restarting" | "error">("idle");
  const restart = async () => {
    if (state === "restarting") return;
    setState("restarting");
    try {
      await api.restartKernel({ investigationId, notebookPath });
      setState("idle");
    } catch (e) {
      console.error("restartKernel failed", e);
      setState("error");
    }
  };
  const label =
    state === "restarting"
      ? "kernel py3.12 restarting…"
      : state === "error"
        ? "kernel py3.12 error"
        : "kernel py3.12 idle";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span>{label}</span>
      <button
        type="button"
        onClick={() => void restart()}
        disabled={state === "restarting"}
        title="Restart kernel"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 3,
          padding: "0 6px",
          height: 18,
          borderRadius: 3,
          background: "transparent",
          border: "1px solid rgba(255,255,255,0.2)",
          color: "var(--text-dark)",
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          cursor: state === "restarting" ? "wait" : "pointer",
        }}
      >
        <Icon name="refresh" size={10} color="var(--text-dark)" />
        Restart
      </button>
    </span>
  );
}
