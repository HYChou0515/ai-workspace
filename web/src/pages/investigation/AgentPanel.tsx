/**
 * Right-column agent panel. Hydrates from /conversation, streams replies
 * via POST /investigations/{id}/messages, renders the design's mix of
 * user / agent / tool-call entries, with suggestion chips + composer.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";

import { api } from "../../api";
import { investigationFileService } from "../../api/fileService";
import { kbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { downloadChatExport } from "../../api/workflows";
import { EntryView } from "../../components/AgentEntryView";
import { HealthDot } from "../../components/HealthDot";
import { Icon } from "../../components/Icon";
import { ModelEffortPicker } from "../../components/ModelEffortPicker";
import { SkillsModal } from "../../components/SkillsModal";
import { WorkflowsModal } from "../../components/WorkflowsModal";
import { ToolsPickerModal } from "../../components/ToolsPickerModal";
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
import { type AttachProgress, attachPrompt, runAttach, uploadPathFor } from "./attach";
import { extractClipboardFiles, isImage, readTransferEntries } from "./transfer";

export function AgentPanel({
  investigationId,
  agent: agentProp,
  width = 380,
  fill = false,
  suggestions,
  picker,
  attachedPreset,
  onAttachPreset,
  appTitle,
  appIcon,
  appColor,
  onNewChat,
  onSteer,
  onSaveToolPrefs,
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
  /** #288: when set, this is a workflow RUN chat — the composer STEERS the run (the
   * text becomes a free-text instruction the read-only steerer turns into a reviewable
   * plan) instead of starting a normal interactive turn. Absent → ordinary chat (RCA,
   * KB, free chats). */
  onSteer?: (text: string) => void;
  /** #322: persist this item's per-tool override (`attached_tool_prefs`). Threaded
   * to the header's Tools picker; absent → no picker button. */
  onSaveToolPrefs?: (prefs: Record<string, boolean>) => void;
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
  // #364: attached images show as removable preview chips instead of a raw path in
  // the box; each holds the uploaded workspace `path` (appended to the message on send
  // so the agent can read_image it) + an object-URL `url` for the thumbnail.
  const [imageChips, setImageChips] = useState<{ id: string; path: string; url: string }[]>([]);
  const chipSeq = useRef(0);
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

  // #364: images upload immediately (same as a file drop) but surface as thumbnail
  // chips rather than a path in the box; the path is appended to the message on send.
  const doAttachImages = async (images: File[]) => {
    if (!images.length || attaching) return;
    setProgress({
      loadedBytes: 0,
      totalBytes: images.reduce((n, f) => n + f.size, 0),
      doneFiles: 0,
      totalFiles: images.length,
    });
    try {
      const res = await runAttach({
        files: images,
        uploadDir,
        upload: (path, file, onChunk) =>
          api.uploadFile(slug, investigationId, path, file, {
            onProgress: (loaded) => onChunk?.(loaded),
          }),
        onProgress: setProgress,
      });
      // runAttach derives each path via uploadPathFor, so re-deriving pairs an uploaded
      // path back to its source blob for the thumbnail.
      const byPath = new Map(images.map((f) => [uploadPathFor(uploadDir, f), f]));
      const fresh = res.uploaded.map((path) => ({
        id: `${chipSeq.current++}`,
        path,
        url: URL.createObjectURL(byPath.get(path) ?? new Blob()),
      }));
      if (fresh.length) setImageChips((prev) => [...prev, ...fresh]);
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
      queryClient.invalidateQueries({ queryKey: qk.workspaceUsage(slug, investigationId) });
    }
  };

  // #364: route a drop / paste / picker batch — images → chip flow, others → path flow.
  // Sequential so the two flows don't race on the shared `progress`/`attaching` state.
  const handleIncoming = async (files: File[]) => {
    const images = files.filter(isImage);
    const others = files.filter((f) => !isImage(f));
    if (images.length) await doAttachImages(images);
    if (others.length) await doAttach(others);
  };

  const removeImageChip = (id: string) =>
    setImageChips((prev) => {
      const gone = prev.find((c) => c.id === id);
      if (gone) URL.revokeObjectURL(gone.url);
      return prev.filter((c) => c.id !== id);
    });

  const clearImageChips = () =>
    setImageChips((prev) => {
      prev.forEach((c) => URL.revokeObjectURL(c.url));
      return [];
    });

  const submit = () => {
    const text = draft.trim();
    if (log.streaming) return;
    // #288: in a workflow run chat the composer steers the run — the text is a
    // free-text instruction, not an interactive turn. (Stop the run from the
    // progress bar above (#331); the composer is inert while a turn streams.)
    if (onSteer) {
      if (!text) return;
      setDraft("");
      onSteer(text);
      return;
    }
    // A message that @-mentions people is a summon — it notifies them and does
    // NOT run the agent (the draft becomes the note).
    if (mentions.length > 0) {
      void mention(mentions, text);
      setMentions([]);
      setDraft("");
      return;
    }
    // #364: image chips carry their workspace path — prepend them so the agent gets
    // the paths (it only ever sees paths). A message with only images is valid.
    const imagePaths = imageChips.map((c) => c.path);
    if (!text && !imagePaths.length) return;
    const body = imagePaths.length ? [attachPrompt(imagePaths), text].filter(Boolean).join("\n\n") : text;
    setDraft("");
    clearImageChips();
    void send(body);
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
        onSaveToolPrefs={onSaveToolPrefs}
      />

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
            // #285: resolve workspace paths so a tool card renders the charts it
            // wrote inline (this item's files endpoint).
            fileUrl={(p) => api.fileContentUrl(slug, investigationId, p)}
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
        // #364: recurse dropped folders (webkitGetAsEntry) and route through the same
        // image-vs-file split as the picker + paste.
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void readTransferEntries(e.dataTransfer).then((files) => {
            if (files.length) void handleIncoming(files);
          });
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
        {imageChips.length > 0 && (
          <div
            data-testid="image-chips"
            style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "flex-start" }}
          >
            {imageChips.map((c) => (
              <div
                key={c.id}
                data-testid="image-chip"
                title={c.path}
                style={{
                  position: "relative",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 2,
                  maxWidth: 84,
                }}
              >
                <img
                  src={c.url}
                  alt={c.path}
                  style={{
                    width: 48,
                    height: 48,
                    objectFit: "cover",
                    borderRadius: "var(--radius-chip)",
                    border: "1px solid var(--paper-3)",
                  }}
                />
                <span
                  style={{
                    fontSize: pxToRem(10),
                    color: "var(--text-paper-d)",
                    maxWidth: 84,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {c.path.split("/").pop()}
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${c.path}`}
                  onClick={() => removeImageChip(c.id)}
                  style={{
                    position: "absolute",
                    top: -6,
                    right: -6,
                    background: "var(--white)",
                    border: "1px solid var(--paper-3)",
                    borderRadius: "50%",
                    lineHeight: 0,
                    padding: 2,
                    cursor: "pointer",
                  }}
                >
                  <Icon name="x" size={10} />
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={composerRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onComposerKeyDown}
          // #364: paste an image (screenshot) → chip; paste a file → path; plain text
          // falls through untouched (we only intercept when the clipboard carries files).
          onPaste={(e) => {
            const { images, files } = extractClipboardFiles(e.clipboardData, Date.now());
            const all = [...images, ...files];
            if (all.length) {
              e.preventDefault();
              void handleIncoming(all);
            }
          }}
          placeholder={
            onSteer
              ? "Tell the run what to change (e.g. use the X collection, redo from ingest)…"
              : mentions.length > 0
                ? "Add a note (optional)…"
                : "Ask the agent…"
          }
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
              if (files.length) void handleIncoming(files);
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
              if (files.length) void handleIncoming(files);
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
  onSaveToolPrefs,
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
  /** #322: persist this item's per-tool override (`attached_tool_prefs`) via the
   * parent's read-modify-PUT. Present → the Tools picker button shows; absent →
   * no picker (e.g. surfaces with no item to persist onto). */
  onSaveToolPrefs?: (prefs: Record<string, boolean>) => void;
}) {
  const t = useT();
  const [exportError, setExportError] = useState<string | null>(null);
  const [showSkills, setShowSkills] = useState(false);
  const [showWorkflows, setShowWorkflows] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const fileService = useMemo(
    () => investigationFileService(slug, investigationId),
    [slug, investigationId],
  );
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
      {showSkills && (
        <SkillsModal
          slug={slug}
          itemId={investigationId}
          fileService={fileService}
          onClose={() => setShowSkills(false)}
        />
      )}
      {showWorkflows && (
        <WorkflowsModal
          slug={slug}
          itemId={investigationId}
          fileService={fileService}
          onClose={() => setShowWorkflows(false)}
        />
      )}
      {showTools && onSaveToolPrefs && (
        <ToolsPickerModal
          slug={slug}
          itemId={investigationId}
          onSave={onSaveToolPrefs}
          onClose={() => setShowTools(false)}
        />
      )}
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
      {onSaveToolPrefs && (
        <button
          type="button"
          // #322: open the per-item tool picker — choose (tri-state) which App tools
          // the assistant can use in this workspace. Only shown when the parent can
          // persist the override.
          data-testid="tools-button"
          onClick={() => setShowTools(true)}
          title={t("tools.button.tip")}
          aria-label={t("tools.button")}
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
          <Icon name="settings" size={13} /> {t("tools.button")}
        </button>
      )}
      <button
        type="button"
        // #298: open the Skills panel — see / download / import the skills the user
        // co-created here (the IDE tree hides the `.skill/` dot-folder).
        data-testid="skills-button"
        onClick={() => setShowSkills(true)}
        title={t("skills.tip")}
        aria-label={t("skills.button")}
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
        <Icon name="sparkle" size={13} /> {t("skills.button")}
      </button>
      <button
        type="button"
        // #323: open the Workflows panel — run / download / import the workflows the
        // user co-created here (the IDE tree hides the `.workflows/` dot-folder).
        data-testid="workflows-button"
        onClick={() => setShowWorkflows(true)}
        title={t("workflows.tip")}
        aria-label={t("workflows.button")}
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
        <Icon name="layers" size={13} /> {t("workflows.button")}
      </button>
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
