/**
 * F11 — Report renderer. Reads the file listing to derive available
 * versions, lets the user switch between them, and renders the selected
 * version's markdown body. Shows a "superseded" overlay when the user
 * is viewing anything but the current version.
 */

import { useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { useFileList, useFileService } from "../../api/fileService";
import {
  useFileBuffer,
  useFileBufferStore,
  useIsDirty,
} from "../../hooks/fileBuffer";
import { useEditMode } from "../../hooks/editMode";
import { useAgent } from "../../hooks/useAgent";
import { Icon } from "../../components/Icon";
import { MonacoEditor } from "../../components/MonacoEditor";
import { docHref } from "../../pages/kb/kbLinks";
import type { MessageCitation } from "../../api/types";
import { buildByMarker, kbCiteAnchor, kbCiteUrlTransform } from "../kbCite";
import { remarkKbCitation } from "./remarkKbCitation";
import {
  type ReportVersion,
  reportVersions,
  versionFromPath,
} from "./versions";
import { pxToRem } from "../../lib/pxToRem";

export function ReportRenderer({ path }: { path: string }) {
  const files = useFileList();
  const { send, log } = useAgent();
  // Dirty + save plumbing for the selected version's edit Buffer — wired
  // here at the ribbon's owner so `selected.path` and the save call stay
  // in sync when the user switches versions while editing.
  const bufferStore = useFileBufferStore();
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
      `Review the current findings (brief, notebooks, ` +
        `rank-factors-*.csv, plots) and write \`/report.v${next}.md\` — a ` +
        `full RCA report that supersedes v${maxV}. Follow the report ` +
        `structure in your system prompt: Problem statement → Findings ` +
        `(each a) conclusion → b) hypothesis → c) data + chart → d) KB ` +
        `references, ordered by physical priority) → Next steps. Every ` +
        `finding carries specific numbers and at least one chart embedded ` +
        `as \`![alt](./<filename>.png)\` — do NOT inline base64 (the ` +
        `renderer cannot display data: URLs).`,
    );
    refresh(); // pick up the freshly-written version
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <RibbonWrapper
        versions={versions}
        selected={selected}
        onSelect={onSelect}
        onExport={onExport}
        onGenerate={() => void onGenerate()}
        generating={log.streaming}
        onSave={() => void bufferStore.save(selected.path)}
      />
      {!selected.isCurrent && (
        <SupersededNotice
          current={versions.find((v) => v.isCurrent) ?? selected}
          onJump={onSelect}
        />
      )}
      <div ref={bodyRef}>
        <ReportBody
          path={selected.path}
          superseded={!selected.isCurrent}
          version={selected.v}
        />
      </div>
      <VersionHistory versions={versions} selected={selected} onSelect={onSelect} />
    </div>
  );
}

/** Thin wrapper that pulls the per-version dirty flag — it can't live in
 * the outer `ReportRenderer` because `useIsDirty(selected.path)` would
 * subscribe to a path that may change at any version switch, and we want
 * the dirty subscription to track the CURRENT path without manual cleanup.
 */
function RibbonWrapper({
  versions,
  selected,
  onSelect,
  onExport,
  onGenerate,
  generating,
  onSave,
}: {
  versions: ReportVersion[];
  selected: ReportVersion;
  onSelect: (v: ReportVersion) => void;
  onExport: () => void;
  onGenerate: () => void;
  generating: boolean;
  onSave: () => void;
}) {
  const dirty = useIsDirty(selected.path);
  return (
    <VersionRibbon
      versions={versions}
      selected={selected}
      onSelect={onSelect}
      onExport={onExport}
      onGenerate={onGenerate}
      generating={generating}
      editPath={selected.path}
      dirty={dirty}
      onSave={onSave}
    />
  );
}

function VersionRibbon({
  versions,
  selected,
  onSelect,
  onExport,
  onGenerate,
  generating,
  editPath,
  dirty,
  onSave,
}: {
  versions: ReportVersion[];
  selected: ReportVersion;
  onSelect: (v: ReportVersion) => void;
  onExport: () => void;
  onGenerate: () => void;
  generating: boolean;
  editPath: string;
  dirty: boolean;
  onSave: () => void;
}) {
  // Edit toggle lives on the ribbon itself rather than in the group tab
  // strip — the report has its own ribbon UI, and squeezing the pencil
  // into a non-existent strip would surprise the user. The same
  // `useEditMode` store still drives both Monaco and the dirty / save
  // plumbing, so split panes on the same path stay in sync.
  const { isEditing, toggle } = useEditMode();
  const editing = isEditing(editPath);
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
      <strong style={{ fontSize: pxToRem(13) }}>Final report</strong>
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
                fontSize: pxToRem(11),
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
      {editing && (
        <button
          type="button"
          onClick={onSave}
          disabled={!dirty}
          title={dirty ? "Save changes (⌘/Ctrl-S)" : "No unsaved changes"}
          className="btn"
          data-variant="primary"
          data-size="sm"
        >
          {dirty ? "Save" : "Saved"}
        </button>
      )}
      <button
        type="button"
        onClick={() => toggle(editPath)}
        title={editing ? "Done editing — switch back to preview" : "Edit this report version"}
        aria-pressed={editing}
        style={{
          padding: "4px 10px",
          border: editing ? "1px solid var(--accent)" : "1px solid var(--ink-4)",
          borderRadius: "var(--radius-btn)",
          background: editing ? "var(--accent)" : "transparent",
          color: editing ? "var(--white)" : "var(--text-dark)",
          fontSize: pxToRem(12),
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        {editing ? "Editing" : "Edit"}
      </button>
      <button
        type="button"
        onClick={onExport}
        title="Print / save the report as PDF"
        className="btn"
        data-variant="secondary"
        data-size="sm"
      >
        Export PDF
      </button>
      <button
        type="button"
        onClick={onGenerate}
        disabled={generating}
        title="Ask the agent to draft the next report version"
        className="btn"
        data-variant="primary"
        data-size="sm"
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
      <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
        Viewing an older version — superseded by v{current.v}. Read-only.
      </span>
      <span style={{ flex: 1 }} />
      <button
        type="button"
        onClick={() => onJump(current)}
        className="btn"
        data-variant="secondary"
        data-size="sm"
      >
        Go to current
      </button>
    </div>
  );
}

function ReportBody({
  path,
  superseded,
  version,
}: {
  path: string;
  superseded: boolean;
  version: number;
}) {
  // Edit-aware buffer so the report shares the dirty/save plumbing with
  // every other markdown surface (brief.md, SOP.md, …). The pencil
  // toggle in the group tab strip flips us into Monaco.
  const { entry, setText } = useFileBuffer(path);
  const { isEditing } = useEditMode();
  const editing = isEditing(path);
  const svc = useFileService();
  const { log } = useAgent();
  const citations = useMemo(() => collectCitations(log.entries), [log.entries]);
  // marker → every citation chunk that shares it (a single `[5]` can map to
  // multiple chunks when re-used). See `buildByMarker` / `kbCiteAnchor` in
  // `../kbCite` for the shared resolution rules.
  const byMarker = useMemo(() => buildByMarker(citations), [citations]);
  // New tab — keeps the investigation in the current tab so the user
  // doesn't lose context when sanity-checking a citation. `docHref`
  // includes the deploy basename so the URL works outside the router.
  const openCitation = (c: MessageCitation) =>
    window.open(docHref(c.document_id, c.snippet), "_blank", "noopener,noreferrer");

  if (entry.status === "loading") {
    return (
      <div style={cardStyle()}>
        <Status>Loading report…</Status>
      </div>
    );
  }
  if (entry.status === "error") {
    return (
      <div style={cardStyle()}>
        <Status tone="err">{entry.error ?? "load failed"}</Status>
      </div>
    );
  }
  if (entry.kind !== "text") {
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
            fontSize: pxToRem(13),
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
      {editing ? (
        <div style={{ minHeight: 320 }}>
          <MonacoEditor
            value={entry.text}
            onChange={setText}
            language="markdown"
            minHeight={320}
          />
        </div>
      ) : (
        <article className="md-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath, remarkKbCitation]}
            rehypePlugins={[rehypeKatex]}
            // Preserve the `kb-cite:N` hrefs remarkKbCitation emits — the default
            // sanitizer would drop them to '' before our `a` handler runs.
            urlTransform={kbCiteUrlTransform}
            components={{
              // Resolve workspace-relative image paths (./xxx.png, xxx.png)
              // to the file API. Without this, `![](./plot.png)` would try
              // to load the page-relative URL and 404.
              img: ({ src, alt }) => {
                const resolved = svc.fileUrl(src, path);
                return (
                  <img
                    src={resolved}
                    alt={alt ?? ""}
                    style={{ maxWidth: "100%", height: "auto" }}
                  />
                );
              },
              // `[N]` markers in the body are transformed by remarkKbCitation
              // into links with `kb-cite:N` urls; the shared `kbCiteAnchor`
              // renders those as inline pills that open the matching KB
              // document on click (`null` ⇒ not a citation, fall through to
              // the normal markdown-link handling below).
              a: ({ href, children, ...rest }) => {
                const cite = kbCiteAnchor({ href, children }, byMarker, openCitation);
                if (cite) return cite;
                // A workspace-relative link (`[chart](/step2-download/abc.png)`)
                // resolves to the file API so the user can open it; external
                // URLs / #fragments pass through. Resolved files open in a new
                // tab (#73).
                const resolved = typeof href === "string" ? svc.fileUrl(href, path) : href;
                const isFile = typeof href === "string" && resolved !== href;
                return (
                  <a
                    href={resolved}
                    {...rest}
                    {...(isFile ? { target: "_blank", rel: "noreferrer" } : {})}
                  >
                    {children}
                  </a>
                );
              },
            }}
          >
            {entry.text}
          </ReactMarkdown>
        </article>
      )}
      <SourcesPanel citations={citations} onOpen={openCitation} />
      <footer
        style={{
          marginTop: 18,
          paddingTop: 10,
          borderTop: "1px solid var(--paper-3)",
          fontFamily: "var(--font-mono)",
          fontSize: pxToRem(11),
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

/**
 * Sources panel — every KB citation surfaced in this investigation's
 * conversation rendered as a clickable card under the report body. The
 * agent writes `[N]` references inline; this block gives them a clickable,
 * styled landing. Click → in-app KB doc viewer (`/kb/doc/{id}?hl=...`).
 *
 * We dedupe by document_id (same chunk cited by several ask_knowledge_base
 * calls collapses to one card) so the panel stays readable across long
 * investigations. Marker numbers are kept from the LATEST tool call that
 * cited the doc — that's the closest to whatever the report's [N] map to.
 */
function SourcesPanel({
  citations,
  onOpen,
}: {
  citations: MessageCitation[];
  onOpen: (c: MessageCitation) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <div className="kb-cites" style={{ marginTop: 16 }}>
      <div className="kb-cites__label">Sources</div>
      {citations.map((c) => (
        <button
          key={`${c.document_id}#${c.marker}`}
          type="button"
          className="kb-cite"
          onClick={() => onOpen(c)}
        >
          <span className="kb-cite__marker">[{c.marker}]</span>
          <span className="kb-cite__body">
            <span className="kb-cite__file">{c.filename}</span>
            <span className="kb-cite__snippet">{c.snippet}</span>
          </span>
          <Icon name="arrow_r" size={12} color="var(--text-paper-d2)" />
        </button>
      ))}
    </div>
  );
}

/**
 * Walk the agent log's entries and harvest every KB citation the BE
 * attached anywhere — both `ask_knowledge_base` TOOL messages (raw KB
 * sub-agent output) AND outer ASSISTANT messages (citations bubbled onto
 * the answer by the BE in `_bubble_kb_citations`). Without walking
 * assistant entries we'd drop the cards for any marker the sub-agent
 * returned but the agent only quoted in its synthesised prose / report.
 *
 * Dedupe by the cited CHUNK (`document_id#start`) so the same passage
 * cited by multiple ask_kb calls collapses to one card. Two truly
 * distinct chunks that happen to share a marker number (call A's `[5]`
 * vs call B's `[5]` against different docs) stay as two cards in the
 * panel — the visual order disambiguates them. Sort by marker so the
 * panel reads `[1] [2] [3] …` left-to-right.
 */
function collectCitations(
  entries: ReturnType<typeof useAgent>["log"]["entries"],
): MessageCitation[] {
  const byChunk = new Map<string, MessageCitation>();
  const add = (c: MessageCitation) => {
    const key = `${c.document_id}#${c.start}`;
    if (!byChunk.has(key)) byChunk.set(key, c);
  };
  for (const e of entries) {
    if (e.kind === "tool_call") {
      for (const c of e.call.citations ?? []) add(c);
    } else if (e.kind === "message") {
      for (const c of e.message.citations ?? []) add(c);
    }
  }
  return Array.from(byChunk.values()).sort((a, b) => a.marker - b.marker);
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
                  fontSize: pxToRem(11),
                }}
              >
                {v.isCurrent ? "current" : "superseded"}
              </span>
              <span style={{ flex: 1, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
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
