/**
 * VSCode-shaped workspace shell. Renders all chrome (top bar, activity
 * bar, sidebar, editor, bottom panel, status bar, agent panel) and
 * owns the file/tab state shared between them.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { FileInfo, Investigation } from "../../api/types";
import { formatInvestigationId } from "../../api/types";
import { Icon, type IconName } from "../../components/Icon";
import { Popover, PopoverDivider, PopoverItem } from "../../components/Popover";
import { RcaMark } from "../../components/RcaMark";
import { SeverityChip, StatusChip } from "../../components/StatusChip";
import { useFileContent } from "../../hooks/useFileContent";
import { usePersistentDeque } from "../../hooks/usePersistentSet";
import { FileView } from "../../renderers/FileView";
import { AgentPanel } from "./AgentPanel";
import { CommandPalette } from "./CommandPalette";
import { basename, breadcrumbSegments, pickRenderer } from "./renderer";

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
}: {
  investigation: Investigation;
  files: FileInfo[];
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
  const [model, setModel] = useState(MODEL_OPTIONS[0]!);

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

  // ⌘P opens the command palette; ⌘B toggles between Evidence and the
  // last mode (cheap convenience). Escape closes everything modal-ish.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "p") {
        e.preventDefault();
        setPaletteOpen(true);
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
          onSettings={() => alert("Settings panel not implemented.")}
        />
        <ActivitySidebar
          mode={activityMode}
          investigation={investigation}
          files={files}
          activePath={activeTab}
          openTabs={openTabs}
          recentFiles={recentFiles.values}
          onOpenFile={openFile}
        />
        <EditorArea
          investigationId={investigation.resource_id}
          openTabs={openTabs}
          activeTab={activeTab}
          onSelectTab={setActiveTab}
          onCloseTab={closeTab}
        />
        <AgentPanel investigationId={investigation.resource_id} />
      </div>

      <CommandPalette
        open={paletteOpen}
        files={files}
        onClose={() => setPaletteOpen(false)}
        onPick={openFile}
      />
    </div>
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
        {(close) => (
          <div style={{ minWidth: 180 }}>
            <PopoverItem onClick={close}>{investigation.owner}</PopoverItem>
            <PopoverDivider />
            <PopoverItem onClick={() => { close(); alert("Sign-out not implemented."); }}>
              Sign out
            </PopoverItem>
          </div>
        )}
      </Popover>
    </div>
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
}: {
  investigation: Investigation;
  files: FileInfo[];
  activePath: string | null;
  openTabs: OpenTab[];
  onOpenFile: (path: string) => void;
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
          <button
            type="button"
            title="Upload file"
            onClick={() =>
              alert("Upload UI not implemented yet — ask the agent to write a file instead.")
            }
            style={{ color: "var(--text-paper-d)" }}
          >
            <Icon name="plus" size={14} />
          </button>
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

function OutlineSection({
  activePath,
  investigationId,
}: {
  activePath: string | null;
  investigationId: string;
}) {
  // Only meaningful for markdown — pull the file content and extract headings.
  const renderable = activePath && pickRenderer(activePath) === "markdown";
  const content = useFileContent(
    investigationId,
    renderable ? activePath : null,
  );

  if (!renderable) {
    return (
      <Section title="Outline">
        <div style={{ padding: "4px 14px", color: "var(--text-paper-d)", fontSize: 12 }}>
          (open a markdown file to see headings)
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
  width: 260,
  flexShrink: 0,
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
}: {
  investigationId: string;
  openTabs: OpenTab[];
  activeTab: string | null;
  onSelectTab: (p: string) => void;
  onCloseTab: (p: string) => void;
}) {
  const [bottomTab, setBottomTab] = useState<"problems" | "output" | "terminal" | "agent_log" | "run_history">("agent_log");

  return (
    <section style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <TabStrip
        tabs={openTabs}
        active={activeTab}
        onSelect={onSelectTab}
        onClose={onCloseTab}
      />
      <Breadcrumb activeTab={activeTab} />
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

      <BottomPanel tab={bottomTab} onTab={setBottomTab} />
      <StatusBar activeTab={activeTab} />
    </section>
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
}: {
  tabs: OpenTab[];
  active: string | null;
  onSelect: (p: string) => void;
  onClose: (p: string) => void;
}) {
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
    </div>
  );
}

function BottomPanel({
  tab,
  onTab,
}: {
  tab: "problems" | "output" | "terminal" | "agent_log" | "run_history";
  onTab: (t: "problems" | "output" | "terminal" | "agent_log" | "run_history") => void;
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
        height: 200,
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
      </div>
      <div
        className="scrollable"
        style={{ flex: 1, overflow: "auto", padding: "8px 14px", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-paper)" }}
      >
        <PanelBody tab={tab} />
      </div>
    </div>
  );
}

function PanelBody({
  tab,
}: {
  tab: "problems" | "output" | "terminal" | "agent_log" | "run_history";
}) {
  if (tab === "problems") {
    return <div style={{ color: "var(--text-paper-d)" }}>No problems detected.</div>;
  }
  if (tab === "output") {
    return <div style={{ color: "var(--text-paper-d)" }}>No build output.</div>;
  }
  if (tab === "terminal") {
    return (
      <div style={{ color: "var(--text-paper-d)" }}>
        Terminal not wired in v1 — use the notebook cells to exec.
      </div>
    );
  }
  if (tab === "run_history") {
    return <div style={{ color: "var(--text-paper-d)" }}>No agent runs yet.</div>;
  }
  // agent_log
  return (
    <>
      <LogLine ts="13:30:01" kind="info" text="agent run started" />
      <LogLine ts="13:30:04" kind="accent" text="tool_start exec head spc.csv" />
      <LogLine ts="13:30:05" kind="muted" text="tool_end (12 lines)" />
      <LogLine ts="13:30:06" kind="warn" text="parse-error retry: closing array" />
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

function StatusBar({ activeTab }: { activeTab: string | null }) {
  const kind = activeTab ? pickRenderer(activeTab) : null;
  const isNotebook = kind === "notebook";
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
      {isNotebook && <span>kernel py3.11 idle</span>}
      <span>UTF-8</span>
      <span>default-user</span>
    </div>
  );
}
