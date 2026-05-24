/**
 * F11 — Report renderer. Reads the file listing to derive available
 * versions, lets the user switch between them, and renders the selected
 * version's markdown body. Shows a "superseded" overlay when the user
 * is viewing anything but the current version.
 */

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { useFiles } from "../../hooks/useInvestigation";
import { useFileContent } from "../../hooks/useFileContent";
import { useAgent } from "../../hooks/useAgent";
import { Icon } from "../../components/Icon";
import {
  type ReportVersion,
  reportVersions,
  versionFromPath,
} from "./versions";

export function ReportRenderer({
  investigationId,
  path,
}: {
  investigationId: string;
  path: string;
}) {
  const files = useFiles(investigationId);
  const { send, log } = useAgent();
  const [selectedPath, setSelectedPath] = useState(path);
  const bodyRef = useRef<HTMLDivElement>(null);

  if (files.kind === "loading") return <Status>Loading versions…</Status>;
  if (files.kind === "error") return <Status tone="err">{files.error.message}</Status>;

  const refresh = files.refresh;
  const versions = reportVersions(files.items);
  if (versions.length === 0) {
    return <Status>No report versions yet. Ask the agent to draft one.</Status>;
  }
  const selected =
    versionFromPath(versions, selectedPath) ??
    versions[versions.length - 1] ??
    null;
  if (!selected) return <Status>Unable to resolve version.</Status>;

  const maxV = Math.max(...versions.map((v) => v.v));

  // Clicking a version chip always gives feedback — even re-clicking the
  // active one scrolls the report into view (so it never feels dead).
  const onSelect = (v: ReportVersion) => {
    setSelectedPath(v.path);
    bodyRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const onExport = () => window.print();

  const onGenerate = async () => {
    if (log.streaming) return;
    const next = maxV + 1;
    await send(
      `Review the current findings (brief, 5-Why, fishbone, notebooks) and ` +
        `write \`/report.v${next}.md\` — a full 8D report that supersedes ` +
        `v${maxV}. Use the file conventions in your system prompt.`,
    );
    refresh(); // pick up the freshly-written version
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <VersionRibbon
        versions={versions}
        selected={selected}
        onSelect={onSelect}
        onExport={onExport}
        onGenerate={() => void onGenerate()}
        generating={log.streaming}
      />
      {!selected.isCurrent && (
        <SupersededNotice
          current={versions.find((v) => v.isCurrent) ?? selected}
          onJump={onSelect}
        />
      )}
      <div ref={bodyRef}>
        <ReportBody
          investigationId={investigationId}
          path={selected.path}
          superseded={!selected.isCurrent}
          version={selected.v}
        />
      </div>
      <VersionHistory versions={versions} selected={selected} onSelect={onSelect} />
    </div>
  );
}

function VersionRibbon({
  versions,
  selected,
  onSelect,
  onExport,
  onGenerate,
  generating,
}: {
  versions: ReportVersion[];
  selected: ReportVersion;
  onSelect: (v: ReportVersion) => void;
  onExport: () => void;
  onGenerate: () => void;
  generating: boolean;
}) {
  // brief pulse on the chip the user just clicked, so a re-click of the
  // already-active version still reads as "registered".
  const [pulse, setPulse] = useState<number | null>(null);
  const click = (v: ReportVersion) => {
    setPulse(v.v);
    window.setTimeout(() => setPulse((p) => (p === v.v ? null : p)), 350);
    onSelect(v);
  };
  return (
    <div
      className="report-ribbon"
      style={{
        background: "var(--ink)",
        color: "var(--text-dark)",
        padding: "10px 16px",
        borderRadius: "var(--radius-card)",
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
      }}
    >
      <Icon name="file" size={14} color="var(--accent)" />
      <strong style={{ fontSize: 13 }}>Final report</strong>
      <div style={{ display: "flex", gap: 4 }}>
        {versions.map((v) => {
          const active = v.v === selected.v;
          return (
            <button
              key={v.v}
              type="button"
              onClick={() => click(v)}
              style={{
                padding: "2px 10px",
                borderRadius: "var(--radius-chip)",
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                background: active ? "var(--accent)" : "transparent",
                color: active ? "var(--white)" : "var(--text-dark-d)",
                border: active ? "1px solid var(--accent)" : "1px solid var(--ink-4)",
                transform: pulse === v.v ? "scale(1.12)" : "scale(1)",
                transition: "transform 0.15s ease",
              }}
            >
              v{v.v} · {v.isCurrent ? "current" : "superseded"}
            </button>
          );
        })}
      </div>
      <span style={{ flex: 1 }} />
      <button
        type="button"
        onClick={onExport}
        title="Print / save the report as PDF"
        style={{
          padding: "4px 10px",
          border: "1px solid var(--ink-4)",
          borderRadius: "var(--radius-btn)",
          color: "var(--text-dark)",
          fontSize: 12,
        }}
      >
        Export PDF
      </button>
      <button
        type="button"
        onClick={onGenerate}
        disabled={generating}
        title="Ask the agent to draft the next report version"
        style={{
          padding: "4px 12px",
          background: generating ? "var(--ink-4)" : "var(--accent)",
          color: "var(--white)",
          borderRadius: "var(--radius-btn)",
          fontSize: 12,
          cursor: generating ? "wait" : "pointer",
        }}
      >
        {generating ? "Generating…" : "Generate new version"}
      </button>
    </div>
  );
}

function SupersededNotice({
  current,
  onJump,
}: {
  current: ReportVersion;
  onJump: (v: ReportVersion) => void;
}) {
  return (
    <div
      style={{
        background: "var(--paper-2)",
        borderLeft: "3px solid var(--text-paper-d2)",
        padding: "8px 12px",
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <Icon name="clock" size={14} color="var(--text-paper-d)" />
      <span style={{ fontSize: 12, color: "var(--text-paper-d)" }}>
        Viewing an older version — superseded by v{current.v}. Read-only.
      </span>
      <span style={{ flex: 1 }} />
      <button
        type="button"
        onClick={() => onJump(current)}
        style={{
          padding: "2px 10px",
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-btn)",
          fontSize: 12,
        }}
      >
        Go to current
      </button>
    </div>
  );
}

function ReportBody({
  investigationId,
  path,
  superseded,
  version,
}: {
  investigationId: string;
  path: string;
  superseded: boolean;
  version: number;
}) {
  const state = useFileContent(investigationId, path);

  if (state.kind === "loading") {
    return (
      <div style={cardStyle()}>
        <Status>Loading report…</Status>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div style={cardStyle()}>
        <Status tone="err">{state.error.message}</Status>
      </div>
    );
  }
  if (state.content.kind !== "text") {
    return (
      <div style={cardStyle()}>
        <Status>Binary report file — cannot display.</Status>
      </div>
    );
  }

  return (
    <div
      className="report-print-target"
      style={{ ...cardStyle(), position: "relative", opacity: superseded ? 0.85 : 1 }}
    >
      {superseded && (
        <div
          style={{
            position: "absolute",
            top: 18,
            right: 24,
            transform: "rotate(-6deg)",
            border: "2px solid var(--text-paper-d2)",
            padding: "4px 14px",
            color: "var(--text-paper-d2)",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            pointerEvents: "none",
          }}
        >
          Superseded
        </div>
      )}
      <div className="caps" style={{ marginBottom: 6 }}>
        RCA report · v{version}
      </div>
      <article className="md-body">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
          {state.content.text}
        </ReactMarkdown>
      </article>
      <footer
        style={{
          marginTop: 18,
          paddingTop: 10,
          borderTop: "1px solid var(--paper-3)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--text-paper-d2)",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>generated by RCA 3.0</span>
        <span>v{version}</span>
      </footer>
    </div>
  );
}

function VersionHistory({
  versions,
  selected,
  onSelect,
}: {
  versions: ReportVersion[];
  selected: ReportVersion;
  onSelect: (v: ReportVersion) => void;
}) {
  return (
    <div
      style={{
        background: "var(--white)",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        padding: "12px 16px",
      }}
    >
      <div className="caps" style={{ marginBottom: 8 }}>
        Version history
      </div>
      {versions
        .slice()
        .reverse()
        .map((v) => {
          const active = v.v === selected.v;
          return (
            <button
              key={v.v}
              type="button"
              onClick={() => onSelect(v)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "8px 0",
                borderBottom: "1px solid var(--paper-3)",
                background: "transparent",
                textAlign: "left",
                borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
                paddingLeft: 8,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontWeight: 600,
                  color: "var(--text-paper)",
                  width: 32,
                }}
              >
                v{v.v}
              </span>
              <span
                style={{
                  padding: "1px 8px",
                  background: v.isCurrent ? "var(--accent-soft)" : "var(--paper-2)",
                  color: v.isCurrent ? "var(--accent-h)" : "var(--text-paper-d)",
                  borderRadius: "var(--radius-chip)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                }}
              >
                {v.isCurrent ? "current" : "superseded"}
              </span>
              <span style={{ flex: 1, fontSize: 12, color: "var(--text-paper-d)" }}>
                {v.path}
              </span>
              <Icon name="chev_r" size={12} color="var(--text-paper-d2)" />
            </button>
          );
        })}
    </div>
  );
}

function cardStyle(): React.CSSProperties {
  return {
    background: "var(--white)",
    border: "1px solid var(--paper-3)",
    borderRadius: "var(--radius-card)",
    padding: "32px 40px",
  };
}

function Status({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "err";
}) {
  return (
    <div
      style={{
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
        fontSize: "var(--text-body)",
      }}
    >
      {children}
    </div>
  );
}
