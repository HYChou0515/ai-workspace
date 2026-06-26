/**
 * Right-column agent panel. Hydrates from /conversation, streams replies
 * via POST /investigations/{id}/messages, renders the design's mix of
 * user / agent / tool-call entries, with suggestion chips + composer.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { downloadChatExport } from "../../api/workflows";
import { EntryView } from "../../components/AgentEntryView";
import { HealthDot } from "../../components/HealthDot";
import { Icon } from "../../components/Icon";
import { ModelEffortPicker } from "../../components/ModelEffortPicker";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { UsageBar } from "./UsageBar";
import { ReplayDialog, type ReplayRequest } from "../../components/ReplayDialog";
import { useDialog } from "../../components/Dialog";
import { Popover } from "../../components/Popover";
import { AppIcon } from "../../components/AppIcon";
import { UserChip } from "../../components/UserChip";
import { UserPicker } from "../../components/UserPicker";
import { docHref } from "../kb/kbLinks";
import { type AgentState, useOptionalAgent } from "../../hooks/useAgent";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { chatEmptyHint } from "../../lib/chatCopy";
import { modCombo } from "../../lib/platform";
import { nameForPreset, pickerModels, presetForName } from "./agentPicker";
import { useStickToBottom } from "../../hooks/useStickToBottom";
import { TurnStatus } from "../../components/TurnStatus";
import { turnsFromEntry } from "./agentLog";
import { pxToRem } from "../../lib/pxToRem";
import { useT } from "../../lib/i18n";
import { type AttachProgress, attachPrompt, runAttach } from "./attach";

export function AgentPanel({
  investigationId,
  agent: agentProp,
  width = 380,
  fill = false,
  phases,
  suggestions,
  picker,
  attachedPreset,
  onAttachPreset,
  appTitle,
  appIcon,
  appColor,
  onNewChat,
  uploadDir = "uploads",
}: {
  investigationId: string;
  /** The agent conversation state. Defaults to the surrounding
   * `<AgentProvider>` (RCA's single chat); the multi-chat shell injects a
   * per-chat `useItemChat()` here so one chat tab drives this panel. */
  agent?: AgentState;
  width?: number;
  /** When true (a workspace=false App), the panel fills the row instead of
   * sitting at its fixed resizable width — it's the only pane. */
  fill?: boolean;
  /** The workflow run's phases (skeleton + live progress) for the linear step
   * bar. Absent / empty → no bar (RCA has no run, so it never shows one). */
  phases?: import("../../api/workflows").PhaseNode[];
  /** Quick-prompt chips from the App manifest (``agent.suggestions``). Each
   * entry has a ``label`` (button text) and a ``prompt`` (sent verbatim). */
  suggestions?: import("../../api/types").Suggestion[];
  /** The App's model picker (``manifest.agent.picker``) — friendly name + the
   * config.yaml preset to attach (#89 candidate 3). */
  picker: { preset: string; name: string }[];
  /** The item's currently-attached preset (``attached_preset``). */
  attachedPreset: string;
  /** Persist a newly-picked preset onto the item (read-modify-PUT). */
  onAttachPreset: (preset: string) => void;
  /** App identity for the panel header (#89) — manifest title/icon/color. */
  appTitle?: string;
  appIcon?: string;
  appColor?: string;
  /** #200: the single-chat-leaning escape hatch. When the multi-chat shell bar
   * is hidden, it threads its "start a fresh chat" action here so the chat header
   * is the lone, low-key place to escape a wedged chat. Absent → no header button
   * (the shell bar already carries a creator, or this is a bare RCA chat). */
  onNewChat?: () => void;
  /** #198: the folder the composer's attach stages files into — the item's profile's
   * `upload_dir` (default `uploads/`), the same folder its workflows glob. */
  uploadDir?: string;
}) {
  // Quick-prompt chips come ONLY from the attached AgentConfig (BE) — the FE
  // never invents its own. No config suggestions → no chip row.
  const slug = useWorkspaceSlug();
  const queryClient = useQueryClient();
  const chips = suggestions ?? [];
  const me = useCurrentUser();
  const ctxAgent = useOptionalAgent();
  const agent = agentProp ?? ctxAgent;
  if (!agent) throw new Error("AgentPanel needs an agent (prop or <AgentProvider>)");
  const { log, send, mention, cancel, undo } = agent;
  const dialog = useDialog();

  // #38: "undo to here" on the user prompt at entry `i` — drop that turn
  // and every later one. Confirm first (it's destructive + irreversible)
  // and say plainly that workspace files aren't reverted.
  const onUndoFromEntry = async (i: number) => {
    if (log.streaming) return;
    const turns = turnsFromEntry(log.entries, i);
    if (turns <= 0) return;
    const choice = await dialog.confirm({
      title: turns === 1 ? "Undo this turn?" : `Undo the last ${turns} turns?`,
      body: "This removes the messages from here on. Files the agent changed in the workspace are not reverted.",
      actions: [
        { id: "undo", label: "Undo", variant: "danger" },
        { id: "cancel", label: "Cancel" },
      ],
    });
    if (choice !== "undo") return;
    try {
      await undo(turns);
    } catch (err) {
      alert(`Undo failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };
  const chatScrollRef = useStickToBottom<HTMLDivElement>(log);
  const t = useT();
  const [draft, setDraft] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  // #198: live upload state for the composer attach — null when idle, else the
  // aggregate byte/file progress driving the bar. `dragging` flags the drop overlay.
  const [progress, setProgress] = useState<AttachProgress | null>(null);
  const [dragging, setDragging] = useState(false);
  // #51 P6: replay diagnostic for one past entry (assistant / tool).
  const [replayReq, setReplayReq] = useState<ReplayRequest | null>(null);
  // Handoff 3.0 composer model picker. Picking a model here CHANGES THE item's
  // attached preset (persistent, every later turn, visible to all members) — the
  // backend AppCatalog resolves it per turn. It is NOT a per-message override.
  // The RCA agent's ask_knowledge_base searches every collection, so the
  // "Search the wiki" toggle is offered when ANY collection builds a wiki.
  const { data: kbCollections = [] } = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => kbApi.listCollections(),
  });
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const attaching = progress !== null;

  // #198: stage one or more files (or a whole folder) into the profile's upload_dir,
  // then drop their path(s) into the draft. Any type, any size — the backend's 413 cap
  // is the only gate; an over-size / failed file is reported and the rest still land.
  const doAttach = async (files: File[]) => {
    if (!files.length || attaching) return;
    setProgress({
      loadedBytes: 0,
      totalBytes: files.reduce((n, f) => n + f.size, 0),
      doneFiles: 0,
      totalFiles: files.length,
    });
    try {
      const res = await runAttach({
        files,
        uploadDir,
        upload: (path, file, onChunk) =>
          api.uploadFile(slug, investigationId, path, file, {
            onProgress: (loaded) => onChunk?.(loaded),
          }),
        onProgress: setProgress,
      });
      if (res.uploaded.length) {
        const ref = attachPrompt(res.uploaded) + "\n\n";
        setDraft((d) => (d ? `${ref}${d}` : ref));
        composerRef.current?.focus();
      }
      // #245: an over-quota (507) rejection is its own line so the user sees
      // "out of space", not a vague size error.
      if (res.overQuota.length) {
        alert(t("workspace.overQuota", { names: res.overQuota.join(", ") }));
      }
      const problems = [
        ...res.tooLarge.map((p) => `${p} — exceeds the size limit`),
        ...res.failed.map((p) => `${p} — upload failed`),
      ];
      if (problems.length) alert(`Some files weren't attached:\n${problems.join("\n")}`);
    } finally {
      setProgress(null);
      // #245: refresh the usage bar — a success grew `used`, a 507 left it full.
      queryClient.invalidateQueries({ queryKey: qk.workspaceUsage(slug, investigationId) });
    }
  };

  const submit = () => {
    const text = draft.trim();
    if (log.streaming) return;
    // A message that @-mentions people is a summon — it notifies them and does
    // NOT run the agent (the draft becomes the note).
    if (mentions.length > 0) {
      void mention(mentions, text);
      setMentions([]);
      setDraft("");
      return;
    }
    if (!text) return;
    setDraft("");
    void send(text);
  };

  const onChip = (label: string) => {
    if (log.streaming) return;
    void send(label);
  };

  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter inserts a newline (standard chat behaviour).
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <aside
      data-testid="agent-panel"
      style={{
        // The panel always sits in a flex COLUMN (ItemChatPanel for a Hub chat,
        // the chat wrapper in WorkspaceShell for RCA), so it must grow on the main
        // (vertical) axis to fill that column's height — otherwise it collapses to
        // its content height and the message list below never becomes a bounded,
        // scrollable region (#109: single-chat workspace had no scrollbar once the
        // panel was wrapped in a column instead of being a direct row child).
        // `fill` only selects the WIDTH behaviour: stretch to the row (chat-only
        // Apps + each Hub chat) vs a fixed, resizable width (RCA's side panel).
        // Longhand flex props (not the `flex` shorthand) so toggling `fill` at
        // runtime doesn't trip React's shorthand/longhand-conflict warning.
        flexGrow: 1,
        flexShrink: fill ? 1 : 0,
        flexBasis: "0%",
        minHeight: 0,
        ...(fill ? { minWidth: 0 } : { width }),
        background: "var(--paper)",
        borderLeft: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <AgentHeader
        streaming={log.streaming}
        investigationId={investigationId}
        slug={slug}
        appTitle={appTitle}
        appIcon={appIcon}
        appColor={appColor}
        onNewChat={onNewChat}
      />
      <ProgressBar phases={phases} />

      <div
        ref={chatScrollRef}
        className="scrollable"
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "14px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 14,
          minHeight: 0,
        }}
      >
        {log.entries.length === 0 && !log.streaming && (
          <div style={{ color: "var(--text-paper-d)", fontSize: pxToRem(13) }}>
            {chatEmptyHint(chips.length > 0)}
          </div>
        )}
        {log.entries.map((e, i) => (
          <EntryView
            key={i}
            entry={e}
            onOpenCitation={(c) =>
              window.open(docHref(c.document_id, c.snippet), "_blank", "noopener,noreferrer")
            }
            // #51 P6: hydrated entries map 1:1 onto the persisted
            // conversation (logFromMessages), so the entry index IS the
            // message index. Hidden while streaming — the in-flight
            // turn isn't persisted yet, so indexes would lie.
            onReplay={
              !log.streaming && (e.kind === "tool_call" || (e.kind === "message" && e.message.role === "assistant"))
                ? () => setReplayReq({ kind: "turn", source: "rca", threadId: investigationId, messageIndex: i })
                : undefined
            }
            // #38: per-turn "undo to here" on each user prompt — removes
            // that turn and everything after it. Hidden while streaming
            // (the in-flight turn isn't persisted yet).
            onUndo={
              !log.streaming && e.kind === "message" && e.message.role === "user"
                ? () => void onUndoFromEntry(i)
                : undefined
            }
          />
        ))}
        <TurnStatus log={log} />
        {log.error && (
          <div
            style={{
              padding: 8,
              border: "1px solid var(--err)",
              borderRadius: "var(--radius-card)",
              color: "var(--err)",
              fontFamily: "var(--font-mono)",
              fontSize: pxToRem(12),
            }}
          >
            {log.error}
          </div>
        )}
      </div>

      {chips.length > 0 && (
        <div
          style={{
            padding: "8px 12px",
            borderTop: "1px solid var(--paper-3)",
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
          }}
        >
          {chips.map((s) => (
          <button
            key={s.label}
            type="button"
            onClick={() => onChip(s.prompt)}
            disabled={log.streaming}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "4px 10px",
              borderRadius: 999,
              border: "1px solid var(--paper-3)",
              background: "var(--white)",
              fontSize: pxToRem(12),
              color: "var(--text-paper)",
              cursor: log.streaming ? "not-allowed" : "pointer",
              opacity: log.streaming ? 0.5 : 1,
            }}
          >
            <Icon name="sparkle" size={12} color="var(--accent)" />
            {s.label}
          </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        // #198: drop files anywhere on the composer to stage them into upload_dir.
        onDragOver={(e) => {
          e.preventDefault();
          if (!dragging) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const files = Array.from(e.dataTransfer.files);
          if (files.length) void doAttach(files);
        }}
        style={{
          padding: 12,
          borderTop: "1px solid var(--paper-3)",
          background: "var(--white)",
          display: "flex",
          flexDirection: "column",
          gap: 6,
          position: "relative",
        }}
      >
        {dragging && (
          <div
            data-testid="attach-drop-overlay"
            style={{
              position: "absolute",
              inset: 0,
              zIndex: 3,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "color-mix(in srgb, var(--accent) 12%, var(--white))",
              border: "2px dashed var(--accent)",
              borderRadius: "var(--radius-btn)",
              fontSize: pxToRem(13),
              color: "var(--accent)",
              pointerEvents: "none",
            }}
          >
            {t("kb.dropToUpload")}
          </div>
        )}
        {/* #245: persistent storage usage gauge so the user sees they're filling up. */}
        <UsageBar slug={slug} itemId={investigationId} />
        {progress && (
          <div data-testid="attach-progress" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div
              style={{ height: 4, background: "var(--paper-3)", borderRadius: 2, overflow: "hidden" }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${progress.totalBytes ? Math.round((progress.loadedBytes / progress.totalBytes) * 100) : 0}%`,
                  background: "var(--accent)",
                  transition: "width 80ms linear",
                }}
              />
            </div>
            <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
              Uploading {progress.doneFiles}/{progress.totalFiles}…
            </span>
          </div>
        )}
        {mentions.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
            <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>Summon:</span>
            {mentions.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setMentions((m) => m.filter((x) => x !== id))}
                title="Remove"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 6px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-chip)",
                  fontSize: pxToRem(12),
                }}
              >
                <UserChip userId={id} size={16} />
                <Icon name="x" size={11} />
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={composerRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onComposerKeyDown}
          placeholder={mentions.length > 0 ? "Add a note (optional)…" : "Ask the agent…"}
          rows={3}
          style={{
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            padding: 8,
            fontSize: pxToRem(13),
            resize: "vertical",
            outline: "none",
            fontFamily: "var(--font-body)",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ModelEffortPicker
            models={pickerModels(picker)}
            selectedName={nameForPreset(picker, attachedPreset)}
            onSelectModel={(name) => {
              const preset = presetForName(picker, name);
              if (preset) onAttachPreset(preset);
            }}
            // Depth applies to this turn's ask_knowledge_base lookups —
            // useAgent sends the sticky selection with every message.
            retrieval
            wikiAvailable={kbCollections.some((c) => c.use_wiki)}
          />
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) void doAttach(files);
            }}
            style={{ display: "none" }}
          />
          <input
            ref={folderInputRef}
            type="file"
            // @ts-expect-error — non-standard but widely supported; mirrors FileTree.
            webkitdirectory=""
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) void doAttach(files);
            }}
            style={{ display: "none" }}
          />
          <Popover
            side="top"
            trigger={({ onClick }) => (
              <button
                type="button"
                onClick={onClick}
                title="@ mention someone to come look (notifies them — no agent run)"
                style={{ color: "var(--text-paper-d)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: pxToRem(12) }}
              >
                <Icon name="user" size={14} /> @
              </button>
            )}
          >
            {() => (
              <div style={{ padding: 8 }}>
                <div className="caps" style={{ padding: "0 4px 6px" }}>
                  Summon people
                </div>
                <UserPicker
                  selected={mentions}
                  exclude={[me]}
                  onToggle={(id) =>
                    setMentions((m) => (m.includes(id) ? m.filter((x) => x !== id) : [...m, id]))
                  }
                />
              </div>
            )}
          </Popover>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attaching}
            title="Attach files (or drop them here)"
            style={{
              color: "var(--text-paper-d)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: pxToRem(12),
            }}
          >
            <Icon name="plus" size={14} />
            {attaching ? "uploading…" : "attach"}
          </button>
          <button
            type="button"
            onClick={() => folderInputRef.current?.click()}
            disabled={attaching}
            title="Attach a whole folder"
            style={{
              color: "var(--text-paper-d)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: pxToRem(12),
            }}
          >
            <Icon name="folder" size={14} /> folder
          </button>
          <span style={{ flex: 1 }} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: pxToRem(10),
              color: "var(--text-paper-d2)",
            }}
          >
            {modCombo("↵")}
          </span>
          {log.streaming ? (
            <button
              type="button"
              onClick={cancel}
              style={{
                padding: "6px 14px",
                borderRadius: "var(--radius-btn)",
                border: "1px solid var(--err)",
                color: "var(--err)",
                fontSize: pxToRem(12),
              }}
            >
              Stop
            </button>
          ) : (
            (() => {
              const summoning = mentions.length > 0;
              const enabled = summoning || draft.trim().length > 0;
              return (
                <button
                  type="submit"
                  disabled={!enabled}
                  style={{
                    padding: "6px 14px",
                    borderRadius: "var(--radius-btn)",
                    background: enabled ? "var(--accent)" : "var(--paper-3)",
                    color: enabled ? "var(--white)" : "var(--text-paper-d)",
                    fontSize: pxToRem(12),
                    fontWeight: 500,
                  }}
                >
                  {summoning ? "Notify" : "Send"}
                </button>
              );
            })()
          )}
        </div>
      </form>
      {replayReq && <ReplayDialog request={replayReq} onClose={() => setReplayReq(null)} />}
    </aside>
  );
}

export function AgentHeader({
  streaming,
  investigationId,
  slug,
  appTitle = "Agent",
  appIcon,
  appColor,
  onNewChat,
}: {
  streaming: boolean;
  investigationId: string;
  /** The current App's slug (#95) — the export targets the app-scoped route. */
  slug: string;
  /** App identity for the agent panel header (#89) — falls back to a generic
   * "Agent" mark when not provided (e.g. in isolated tests). */
  appTitle?: string;
  appIcon?: string;
  appColor?: string;
  /** #200: the single-chat-leaning escape hatch. Present only when the shell bar
   * is hidden, so this header is the lone, low-key way to start a fresh chat and
   * leave a wedged one behind. Absent → no button. */
  onNewChat?: () => void;
}) {
  const [exportError, setExportError] = useState<string | null>(null);
  return (
    <header
      style={{
        padding: "12px 14px",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      {appIcon ? <AppIcon icon={appIcon} color={appColor} size={20} /> : null}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>{appTitle}</div>
        <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
          {/* #159: action cue, not a vague status. Idle = "what do I do now?";
              streaming = an app-neutral "Replying…" (RCA's "investigating" leaked
              the domain into every App). The granular in-turn states live in the
              composer's turn indicator, not here. */}
          {streaming ? "Replying…" : "Your turn — type a message"}
        </div>
      </div>
      {onNewChat && (
        // #200: the low-key escape hatch. A wedged chat (interrupt crash, repetition,
        // step limit, model error) is never a dead end — start a fresh one and the
        // old chat stays reachable via the switcher that appears once a second exists.
        <button
          type="button"
          onClick={onNewChat}
          title="Start a fresh chat"
          aria-label="New chat"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            color: "var(--text-paper-d)",
            fontSize: pxToRem(11),
            background: "transparent",
            border: "none",
            cursor: "pointer",
          }}
        >
          <Icon name="plus" size={13} /> New chat
        </button>
      )}
      <button
        type="button"
        // Downloads the `.chat.json` round-trip format (issue #39): re-uploadable
        // to a KB collection, where the BE runs the same insight extraction the
        // promote path does. Goes through the app-scoped route (#95) and validates
        // the response, so a misroute surfaces an error instead of silently saving
        // the SPA shell as `export-chat.html` (#100). Format details live in code.
        onClick={() => {
          setExportError(null);
          downloadChatExport(slug, investigationId).catch((e) =>
            setExportError(e instanceof Error ? e.message : "匯出失敗"),
          );
        }}
        title="Export this conversation"
        aria-label="Export conversation"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          color: "var(--text-paper-d)",
          fontSize: pxToRem(11),
          background: "transparent",
          border: "none",
          cursor: "pointer",
        }}
      >
        <Icon name="download" size={13} /> Export
      </button>
      {exportError && (
        <span role="alert" style={{ fontSize: pxToRem(11), color: "var(--err)" }}>
          {exportError}
        </span>
      )}
      <HealthDot />
      {/* #159: the running/idle mono badge was the most engineering-flavoured
          chrome in the header and duplicated the status line above. Removed —
          the action cue + the composer's turn indicator carry the state. */}
    </header>
  );
}

/** The color for one phase segment, keyed by its run status. */
function phaseColor(status: string): string {
  if (status === "passed") return "var(--ok)";
  if (status === "running" || status === "awaiting_human") return "var(--accent)";
  if (status === "failed") return "var(--err)";
  return "var(--paper-3)"; // pending / skipped / unknown
}

/**
 * The real linear step bar (topic-hub §12): one segment per workflow phase,
 * colored by its live status, plus a `step n · <title>` label for the current /
 * awaiting phase. No phases (e.g. a free chat, or RCA which has no run) → nothing.
 */
function ProgressBar({ phases }: { phases?: import("../../api/workflows").PhaseNode[] }) {
  if (!phases?.length) return null;

  // The "current" step: the phase the run is on, else the one awaiting a human,
  // else the first not-yet-passed phase, else the last (all done).
  let currentIdx = phases.findIndex((p) => p.current);
  if (currentIdx < 0) currentIdx = phases.findIndex((p) => p.status === "awaiting_human");
  if (currentIdx < 0) currentIdx = phases.findIndex((p) => p.status !== "passed");
  if (currentIdx < 0) currentIdx = phases.length - 1;
  const current = phases[currentIdx];

  return (
    <div
      data-testid="progress-bar"
      style={{
        padding: "8px 14px",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", gap: 4 }}>
        {phases.map((p) => (
          <div
            key={p.id}
            title={p.title}
            style={{
              flex: 1,
              height: 4,
              borderRadius: 2,
              background: phaseColor(p.status),
            }}
          />
        ))}
      </div>
      <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
        step {currentIdx + 1} · {current.title}
      </div>
    </div>
  );
}
