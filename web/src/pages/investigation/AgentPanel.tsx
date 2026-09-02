/**
 * Right-column agent panel. Hydrates from /conversation, streams replies
 * via POST /investigations/{id}/messages, renders the design's mix of
 * user / agent / tool-call entries, with suggestion chips + composer.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

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
import { EnvVarsModal } from "../../components/EnvVarsModal";
import { ToolsPickerModal } from "../../components/ToolsPickerModal";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { UsageBar } from "./UsageBar";
import { ContextBar } from "../../components/ContextBar";
import { parseComposerCommand } from "../../components/composerCommand";
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
import { ConnectionNotice } from "../../components/ConnectionNotice";
import { ResourceLinkText } from "../../components/ResourceLinkText";
import { TurnStatus } from "../../components/TurnStatus";
import { turnLooksSilent, turnsFromEntry } from "./agentLog";
import type { CompactionReason } from "../../api/types";
import type { QuotaKind } from "../../lib/quotaFailure";
import { pxToRem } from "../../lib/pxToRem";
import { useT } from "../../lib/i18n";
import { type AttachProgress, attachPrompt, runAttach, uploadPathFor } from "./attach";
import { extractClipboardFiles, isImage, readTransferEntries } from "./transfer";

/**
 * Max width of the conversation reading column. When the chat pane is wider than
 * this (a workspace=false App filling the row, the IDE collapsed, or the RCA side
 * panel dragged wide), the feed + composer content stay in a centred column with
 * left/right gutters instead of running edge-to-edge. At a narrow panel (RCA's
 * default 380px) the cap never engages, so that layout is untouched. Matches the
 * KB doc viewer's `.kb-docpage__body` cap for a consistent reading measure.
 */
export const CHAT_COLUMN_MAX_W = 860;

/** The centred, capped reading column shared by the feed, chips row and composer. */
const chatColumn: React.CSSProperties = {
  width: "100%",
  maxWidth: CHAT_COLUMN_MAX_W,
  marginLeft: "auto",
  marginRight: "auto",
};

/** The header action buttons (New chat / Tools / Skills / Workflows / Export).
 * `flexShrink: 0` + `whiteSpace: nowrap` keep each button intact so the wrapping
 * header drops a whole button to the next row instead of shrinking it and
 * letting its label wrap character-by-character at the narrow default width (#456). */
const hdrBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  color: "var(--text-paper-d)",
  fontSize: pxToRem(11),
  background: "transparent",
  border: "none",
  cursor: "pointer",
  flexShrink: 0,
  whiteSpace: "nowrap",
};

/** How many image thumbnails the composer will draw. Attaching a folder hands it
 * one chip per image with no natural ceiling; past a couple of rows the extra
 * thumbnails are not telling anyone anything, and the strip is pushing the chat
 * off the screen. The rest are counted, never dropped. */
const CHIP_RENDER_BUDGET = 12;

/** How long a silence goes on before the panel ASKS whether anyone is driving
 * the turn. Comfortably inside the give-up notice's own threshold, so the answer
 * is in hand before the screen would have to say anything — and late enough that
 * a brief hand-off (every turn begins in this state) never costs a request. */
const TURN_ALIVE_ASK_AFTER_MS = 60_000;

/** …and how often it asks again while the silence lasts. Matched to the server's
 * own staleness window (`TURN_STALE_AFTER_MS`), so what the screen shows is
 * never more than one window behind the fact it describes. */
const TURN_ALIVE_REFRESH_MS = 30_000;

/** How many rejected files the composer names before switching to a count. Same
 * failure, one surface over: a folder that is refused wholesale used to render
 * one full path per file into a single line of text. */
const PROBLEM_LIST_BUDGET = 5;

/** "a — x；b — y；…及其他 N 個檔案" — examples, then the number. Naming every
 * rejected file is what turned a bad folder attach into a wall of text under the
 * composer. */
function problemSummary(problems: string[]): string {
  if (problems.length <= PROBLEM_LIST_BUDGET) return problems.join("；");
  const shown = problems.slice(0, PROBLEM_LIST_BUDGET).join("；");
  return `${shown}；…及其他 ${problems.length - PROBLEM_LIST_BUDGET} 個檔案`;
}

/** Which "out of space" line an attach rejection deserves. `workspace` keeps
 *  the pre-existing #245 message; the other two point somewhere else entirely. */
function overQuotaKey(kind: QuotaKind) {
  if (kind === "user") return "workspace.overQuota.user" as const;
  if (kind === "environment") return "workspace.overQuota.env" as const;
  return "workspace.overQuota" as const;
}

export function AgentPanel({
  investigationId,
  chatId,
  readOnly = false,
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
  onSaveSkillPrefs,
  envVars,
  onSaveEnvVars,
  uploadDir = "uploads",
}: {
  investigationId: string;
  /** The chat this panel is showing, when it is showing a named one.
   *
   * Three things need it. Turns are keyed per chat server-side, so a question
   * about the running turn has to name it — absent means the item's DEFAULT
   * chat, whose key is the item id. #739's context gauge reports this chat's
   * window. And Export downloads THIS chat, rather than whichever one happens
   * to be the item's first.
   *
   * Absent on a surface with no chat of its own: the gauge does not render,
   * and Export is guarded rather than required — a button that cannot name its
   * chat should not be there at all, since it could only guess. */
  chatId?: string;
  /** Permission-disclosure: the current user may read the thread but lacks
   * `converse` — the composer is disabled with a hint (the backend also 403s a
   * send, this just makes the lock legible instead of a raw error). */
  readOnly?: boolean;
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
  /** #380: persist this item's per-skill override (`attached_skill_prefs`). Threaded
   * to the header's Skills picker; absent → the picker still lists + applies skills
   * but its Save is a no-op (surfaces with no item to persist onto). */
  onSaveSkillPrefs?: (prefs: Record<string, boolean>) => void;
  /** The item's environment variables + a way to persist them, forwarded to the
   * header's Env panel. Absent → no Env button (surfaces with no item). */
  envVars?: Record<string, string>;
  onSaveEnvVars?: (envVars: Record<string, string>) => void;
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
  const { log, connection, send, mention, cancel, undo } = agent;
  // A one-line answer to "I just did something and nothing happened" — the
  // composer's own feedback channel (Enter during a turn, Stop). Cleared on the
  // next successful send.
  const [composerHint, setComposerHint] = useState<string | null>(null);
  // …and when the turn ends, because every hint this channel carries is about a
  // turn that was running: 「正在停止這一輪…」 and 「回覆還在進行中…」 both describe
  // a state that is over, and both used to sit there until the next send. A
  // present-tense line that never resolves reads as a stop that never finished.
  useEffect(() => {
    if (!log.streaming) setComposerHint(null);
  }, [log.streaming]);
  // Whether any pod is driving this turn. Asked only while the turn is silent in
  // the one way the screen cannot explain (`turnLooksSilent` — the same
  // predicate `TurnStatus` shows the answer under), and only after the wait is
  // long enough to be suspicious: a turn that is visibly producing never costs a
  // request. `null` while unasked or unanswerable — which `TurnStatus` reads as
  // "still cannot tell", not as "no".
  //
  // And asked REPEATEDLY, because the recorded fact expires (30s server-side)
  // while the answer is shown from ten minutes onwards. A single sample would
  // have one moment near the start of the silence speaking for the whole of it —
  // so a pod that died at minute two would still read as 「還在跑」 at minute
  // twenty, with the retry withheld. That is the endless wait again, now with a
  // server-side fact behind it, which is worse than the guess it replaced.
  const [turnAlive, setTurnAlive] = useState<boolean | null>(null);
  const silent = turnLooksSilent(log);
  useEffect(() => {
    if (!silent) {
      setTurnAlive(null);
      return;
    }
    let cancelled = false;
    const ask = () => {
      void api
        .turnAlive(slug, investigationId, chatId)
        .then((v) => !cancelled && setTurnAlive(v))
        .catch(() => undefined);
    };
    let repeat: ReturnType<typeof setInterval> | undefined;
    const first = setTimeout(() => {
      ask();
      repeat = setInterval(ask, TURN_ALIVE_REFRESH_MS);
    }, TURN_ALIVE_ASK_AFTER_MS);
    return () => {
      cancelled = true;
      clearTimeout(first);
      if (repeat !== undefined) clearInterval(repeat);
    };
  }, [silent, slug, investigationId, chatId]);
  // #739: `/compact` and the button below are the SAME call — a slash command is
  // invisible by nature, so the button is what makes it discoverable, and one
  // route means the two can never drift apart.
  const compact = useMutation({
    mutationFn: () => api.compactChat(slug, investigationId, chatId ?? ""),
    onSuccess: (r) => {
      if (!r.compacted) {
        // Two opposite diagnoses used to share one sentence. Only the second is
        // something the reader can act on, and saying the first over a thread
        // full of history sends them looking for something that is not there.
        // Each refusal gets its own sentence. `failed` is the one that matters
        // most: it is the DOMINANT outcome when the button is pressed on a very
        // full thread (the span is largest exactly then), and it used to be
        // reported as 「沒有需要壓縮的內容」 — the same lie, over a thread full
        // of history, that this whole mechanism was built to stop telling.
        const said: Partial<Record<CompactionReason, string>> = {
          "no-room":
              "這個環境的提示詞本身已經佔滿模型的可讀範圍,整理對話幫不上忙 —— 需要調大模型視窗或縮短提示詞。",
          failed: "整理沒有成功,對話沒有更動。可以再試一次。",
          unavailable: "這個環境沒有開啟整理功能。",
        };
        setComposerHint(said[r.reason] ?? "這段對話還沒有需要壓縮的內容。");
        return;
      }
      void queryClient.invalidateQueries({ queryKey: qk.itemChat(slug, investigationId, chatId ?? "") });
      void queryClient.invalidateQueries({ queryKey: qk.chatContext(slug, investigationId, chatId ?? "") });
    },
  });
  // Messages on a shared item SERIALIZE server-side; they do not cancel each
  // other (#43). So a turn started by SOMEONE ELSE is no reason to lock this
  // viewer out — the backend will happily queue behind it, and taking that away
  // left a spectator with a spinner they did not start and a box they could not
  // type in. Your own in-flight turn still blocks: Stop is the affordance there.
  const othersTurn = log.streaming && log.streamingBy != null && log.streamingBy !== me;
  // Standing note for as long as their turn runs: you may send, and what will
  // happen if you do.
  /** Abandon a stalled attempt and ask the same question again.
   *
   * Cancel FIRST: the backend serializes turns, so sending without stopping
   * would queue the retry behind the very turn that is stuck. The question is
   * taken from the thread rather than the draft box, which the user may already
   * have typed something else into. */
  const retryTurn = () => {
    const asked = [...log.entries]
      .reverse()
      .find((e) => e.kind === "message" && e.message.role === "user");
    if (asked?.kind !== "message") return;
    cancel();
    setComposerHint("已中止並重新提問。");
    void send(asked.message.content);
  };

  const queueNote = othersTurn
    ? `${log.streamingBy} 正在對話中。你現在送出的訊息會排在後面。`
    : null;
  // The strip under the composer says one thing at a time: what just happened to
  // an attachment if anything did, otherwise why a send would queue.
  const composerNote = composerHint ?? queueNote;
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
    // #370: bring the undone prompt back into the composer so the user can tweak
    // and resend it instead of retyping. Capture it BEFORE undo re-fetches and
    // rebuilds the log — entry `i` is the prompt we're rewinding TO (the earliest
    // of the removed turns). Restore only on success (a failed undo mustn't wipe
    // whatever the user was drafting).
    const undone = log.entries[i];
    const restore =
      undone?.kind === "message" && undone.message.role === "user" ? undone.message.content : "";
    try {
      await undo(turns);
      setDraft(restore);
      composerRef.current?.focus();
    } catch (err) {
      setComposerHint(`復原失敗：${err instanceof Error ? err.message : String(err)}`);
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
  // #380: skills the user queued from the Skills panel to APPLY this turn — a one-shot
  // set surfaced as accent chips near the composer and cleared once the message sends.
  const [appliedSkills, setAppliedSkills] = useState<string[]>([]);
  const toggleApplySkill = (name: string) =>
    setAppliedSkills((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  // #51 P6: replay diagnostic for one past entry (assistant / tool).
  const [replayReq, setReplayReq] = useState<ReplayRequest | null>(null);
  // Handoff 3.0 composer model picker. Picking a model here CHANGES THE item's
  // attached preset (persistent, every later turn, visible to all members) — the
  // backend AppCatalog resolves it per turn. It is NOT a per-message override.
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const attaching = progress !== null;

  // #198: stage one or more files (or a whole folder) into the profile's upload_dir,
  // then drop their path(s) into the draft. Any type, any size — the backend's 413 cap
  // is the only gate; an over-size / failed file is reported and the rest still land.
  /** Did `path` actually land? Answers the inconclusive upload outcomes (a
   * network drop or a gateway status arrives after the body was sent, so the
   * file may well be on disk) instead of accusing the upload of failing. */
  const attachmentLanded = async (path: string): Promise<boolean> => {
    const listed = await api.listFiles(slug, investigationId);
    return listed.some((f) => f.path === path || f.path === `/${path}`);
  };

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
        verify: attachmentLanded,
      });
      if (res.uploaded.length) {
        const ref = attachPrompt(res.uploaded) + "\n\n";
        setDraft((d) => (d ? `${ref}${d}` : ref));
        composerRef.current?.focus();
      }
      // #245: an over-quota (507) rejection is its own line so the user sees
      // "out of space", not a vague size error — and WHICH quota, because the
      // remedies are different places (this item / another of yours / close an
      // environment). One message for all three is what the review found.
      // An OS alert() interrupts, cannot be re-read, and is the one piece of UI
      // that cannot say WHICH message it belongs to. Keep the report in the
      // composer, next to the box the files were dropped on.
      const problems = [
        ...res.overQuota.map((p) => `${p} — ${t(overQuotaKey(res.overQuotaKind), { names: p })}`),
        // Don't assert WHOSE limit: a 413 can come from a proxy in front of the
        // app (ingress-nginx defaults to 1 MB) as easily as from the app's own
        // cap, and "exceeds the size limit" on a 3 MB file sends the user hunting
        // for a setting that was never the problem.
        ...res.tooLarge.map((p) => `${p} — 伺服器拒收（檔案過大，或代理設定的上限較低）`),
        ...res.failed.map((p) => `${p} — 上傳失敗`),
      ];
      if (problems.length) setComposerHint(`部分檔案未附加：${problemSummary(problems)}`);
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
        verify: attachmentLanded,
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
        setComposerHint(
          t(overQuotaKey(res.overQuotaKind), { names: res.overQuota.join(", ") }),
        );
      }
      const problems = [
        ...res.tooLarge.map((p) => `${p} — 伺服器拒收（檔案過大，或代理設定的上限較低）`),
        ...res.failed.map((p) => `${p} — 上傳失敗`),
      ];
      if (problems.length) setComposerHint(`部分檔案未附加：${problemSummary(problems)}`);
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
    if (log.streaming && !othersTurn) {
      // Pressing Enter mid-turn used to do NOTHING — the textarea stays enabled,
      // so the user types a whole message, hits Enter, and gets no reaction at
      // all. During any of the stuck states that is indistinguishable from the
      // app being dead. Keep the draft (retyping it is the insult on top) and say
      // why.
      setComposerHint("回覆還在進行中。等它完成，或按 Stop 中止後再送出。");
      return;
    }
    setComposerHint(null);
    // #739: a slash command is not a message. It never reaches the model and is
    // never persisted as something the user said — the literal text "/compact"
    // must not end up in the transcript the summariser is about to read.
    if (chatId && parseComposerCommand(text) === "compact") {
      setDraft("");
      compact.mutate();
      return;
    }
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
    // #364: image chips carry their workspace path. We prepend an `Attached
    // \`path\`` note (a text-only model reaches the image via read_image) AND
    // pass the paths structurally in `imagePaths` — a VLM main model has the BE
    // inline the actual image, so it sees the pixels directly. A message with
    // only images is valid.
    const imagePaths = imageChips.map((c) => c.path);
    if (!text && !imagePaths.length) return;
    const body = imagePaths.length ? [attachPrompt(imagePaths), text].filter(Boolean).join("\n\n") : text;
    setDraft("");
    clearImageChips();
    // #380: hand this turn's queued skills to `send`, then clear them — apply is
    // one-shot (the next turn starts with an empty apply set).
    void send(body, { applySkills: appliedSkills, imagePaths });
    setAppliedSkills([]);
  };

  // grill-me: which `ask_user` questions already have an answer, so an
  // answered card shows the answer instead of inviting a second, contradictory
  // one (two tabs, or a scroll back to an older question).
  const answeredQuestions = useMemo(() => {
    const out: Record<string, string> = {};
    for (const e of log.entries) {
      if (e.kind === "message" && e.message.answers) {
        out[e.message.answers] = e.message.content;
      }
    }
    return out;
  }, [log.entries]);

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
        chatId={chatId}
        slug={slug}
        appTitle={appTitle}
        appIcon={appIcon}
        appColor={appColor}
        onNewChat={onNewChat}
        onSaveToolPrefs={onSaveToolPrefs}
        onSaveSkillPrefs={onSaveSkillPrefs}
        envVars={envVars}
        onSaveEnvVars={onSaveEnvVars}
        appliedSkills={appliedSkills}
        onToggleApplySkill={toggleApplySkill}
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
          minHeight: 0,
        }}
      >
        {/* #108: keep the message column at a readable measure — when the pane is
            wider than the reading column it centres with left/right gutters rather
            than stretching the text edge-to-edge. The scrollbar stays at the pane
            edge; only the content is capped. */}
        <div
          data-testid="chat-column"
          style={{ ...chatColumn, display: "flex", flexDirection: "column", gap: 14 }}
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
            // grill-me: answering an `ask_user` question is an ordinary send
            // that records which question it answers.
            onAnswerQuestion={(a) => {
              if (log.streaming) return;
              void send(a.content, { answers: a.answers });
            }}
            answeredQuestions={answeredQuestions}
            // #583: who is reading, so their own messages align right.
            currentUser={me}
            onOpenCitation={(c) =>
              window.open(docHref(c.document_id, c.snippet), "_blank", "noopener,noreferrer")
            }
            // Permission-disclosure: ask the withheld collection's owner for access.
            // Asking for access is a request to a PERSON — the one action here
            // with a human on the other end. Firing it into the void gave no way
            // to tell "sent" from "silently failed", so the user's only recourse
            // was to press it again and hope.
            onRequestAccess={(w) => {
              void kbApi.requestCollectionAccess(w.collection_id).then(
                () => setComposerHint("已送出存取申請,等待對方回覆。"),
                (err: unknown) =>
                  setComposerHint(
                    `申請存取失敗:${err instanceof Error ? err.message : String(err)}`,
                  ),
              );
            }}
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
        <ConnectionNotice connection={connection} />
        <TurnStatus log={log} onRetry={othersTurn ? undefined : retryTurn} alive={turnAlive} />
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
            {/* #692: when the refusal names /my-resources, that name is the way
                there — a message that only TELLS you where to go leaves the
                #688 "refuse outright" trade-off resting on a page you have to
                go and find. */}
            <ResourceLinkText text={log.error} />
          </div>
        )}
        </div>
      </div>

      {composerNote && (
        <div
          data-testid="composer-hint"
          role="status"
          style={{
            padding: "6px 12px 0",
            fontSize: pxToRem(12),
            color: "var(--text-paper-d)",
          }}
        >
          {/* #692: an attach refused for a resource limit names /my-resources —
              render it so that name is the way there, not just a signpost. */}
          <ResourceLinkText text={composerNote} />
        </div>
      )}

      {chips.length > 0 && (
        <div style={{ padding: "8px 12px", borderTop: "1px solid var(--paper-3)" }}>
          <div style={{ ...chatColumn, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {chips.map((s) => (
          <button
            key={s.label}
            type="button"
            onClick={() => onChip(s.prompt)}
            // A read-only viewer could still fire a chip, and got a raw
            // "send failed: 403" for it — the textarea beside it was already
            // disabled for exactly this reason.
            disabled={log.streaming || readOnly}
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
        {/* The composer content shares the feed's centred reading column so a wide
            pane doesn't put a full-width input under a narrow message column. The
            drop overlay above stays a direct form child so it still covers the
            whole bar. */}
        <div
          data-testid="composer-column"
          style={{ ...chatColumn, display: "flex", flexDirection: "column", gap: 6 }}
        >
        {/* #245: persistent storage usage gauge so the user sees they're filling up. */}
        <UsageBar slug={slug} itemId={investigationId} />
        {/* #739: and how full the CONTEXT window is — the other ceiling a
            long session runs into, and the one that used to arrive as a
            surprise rather than as a gauge. */}
        {chatId && <ContextBar slug={slug} itemId={investigationId} chatId={chatId} />}
        {chatId && (
          <button
            type="button"
            data-testid="compact-chat"
            onClick={() => compact.mutate()}
            disabled={compact.isPending || log.streaming}
            style={{
              alignSelf: "flex-start",
              background: "none",
              border: "none",
              padding: 0,
              cursor: compact.isPending ? "default" : "pointer",
              fontSize: pxToRem(11),
              color: "var(--text-paper-d)",
              textDecoration: "underline",
            }}
          >
            {compact.isPending ? "整理中…" : "整理成摘要"}
          </button>
        )}
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
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              alignItems: "flex-start",
              // A folder attach hands this list however many images the folder
              // held. Two rows' worth, then it scrolls — without a ceiling the
              // strip grows until the composer, and the conversation above it,
              // are off the screen.
              maxHeight: 168,
              overflowY: "auto",
            }}
          >
            {imageChips.slice(0, CHIP_RENDER_BUDGET).map((c) => (
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
            {imageChips.length > CHIP_RENDER_BUDGET && (
              // The COUNT, not just a shortened strip: every one of these is
              // attached and will be sent, so hiding some without saying so
              // would misreport what the message carries.
              <span
                data-testid="image-chips-overflow"
                style={{
                  alignSelf: "center",
                  fontSize: pxToRem(12),
                  color: "var(--text-paper-d)",
                  whiteSpace: "nowrap",
                }}
              >
                +{imageChips.length - CHIP_RENDER_BUDGET} more attached
              </span>
            )}
          </div>
        )}
        {appliedSkills.length > 0 && (
          // #380: the queued-for-this-turn skills. Accent-filled so they read as a
          // distinct kind of chip from the outlined @mention chips, the image
          // thumbnails, and the suggestion prompt chips — this is "the agent will
          // follow these skills this turn", one-shot, removable.
          <div
            data-testid="applied-skill-chips"
            style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}
          >
            <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
              {t("skills.applied")}
            </span>
            {appliedSkills.map((name) => (
              <span
                key={name}
                data-testid="applied-skill-chip"
                title={t("skills.appliedTip")}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 8px",
                  borderRadius: "var(--radius-chip)",
                  background: "var(--accent)",
                  color: "var(--white)",
                  fontSize: pxToRem(12),
                }}
              >
                <Icon name="sparkle" size={11} />
                {name}
                <button
                  type="button"
                  aria-label={`Remove ${name}`}
                  onClick={() => toggleApplySkill(name)}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "var(--white)",
                    lineHeight: 0,
                    padding: 0,
                    cursor: "pointer",
                  }}
                >
                  <Icon name="x" size={10} />
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={composerRef}
          value={draft}
          disabled={readOnly}
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
            readOnly
              ? "You don't have permission to send messages in this workspace."
              : onSteer
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
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", rowGap: 8 }}>
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
              onClick={() => {
                cancel();
                // Stop's ENTIRE feedback used to be the spinner disappearing,
                // which reads the same as the turn finishing on its own. So the
                // click still says something — but about the CLICK, not the
                // outcome: the transcript already gets a 「已取消。」 banner when
                // the turn actually stops, and saying it here too is how one
                // press of Stop came to print the same news twice.
                setComposerHint("正在停止這一輪…");
              }}
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
              const enabled = !readOnly && (summoning || draft.trim().length > 0);
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
  chatId,
  appTitle = "Agent",
  appIcon,
  appColor,
  onNewChat,
  onSaveToolPrefs,
  onSaveSkillPrefs,
  envVars,
  onSaveEnvVars,
  appliedSkills = [],
  onToggleApplySkill,
}: {
  streaming: boolean;
  investigationId: string;
  /** The current App's slug (#95) — the export targets the app-scoped route. */
  slug: string;
  /** The chat this header belongs to. Optional because a surface can have no
   * chat of its own (#739's gauge is absent there for the same reason), and
   * Export is then not drawn: it used to know only the item, which let the
   * server fall back to the item's default chat and hand back the earliest
   * conversation whatever was on screen. No button is better than one that has
   * to guess which conversation it means. */
  chatId?: string;
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
  /** #380: persist this item's per-skill override (`attached_skill_prefs`). Absent →
   * the Skills panel still lists + applies, but its Save is a no-op. */
  onSaveSkillPrefs?: (prefs: Record<string, boolean>) => void;
  /** The item's environment variables, handed to the tools it runs. */
  envVars?: Record<string, string>;
  /** Persist them. Absent → no Env button, the same way the Tools picker is
   * withheld on a surface that cannot persist onto an item. */
  onSaveEnvVars?: (envVars: Record<string, string>) => void;
  /** #380: skills queued (composer-owned) to apply this turn — lit in the panel. */
  appliedSkills?: string[];
  /** #380: toggle a skill in this turn's apply set (composer state lives in AgentPanel). */
  onToggleApplySkill?: (name: string) => void;
}) {
  const t = useT();
  const [exportError, setExportError] = useState<string | null>(null);
  const [showSkills, setShowSkills] = useState(false);
  const [showWorkflows, setShowWorkflows] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [showEnv, setShowEnv] = useState(false);
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
        // At the narrow default panel width the action buttons drop to a second
        // row instead of overlapping the title (#456).
        flexWrap: "wrap",
      }}
    >
      {showSkills && (
        <SkillsModal
          slug={slug}
          itemId={investigationId}
          fileService={fileService}
          onClose={() => setShowSkills(false)}
          onSaveSkillPrefs={onSaveSkillPrefs}
          appliedSkills={appliedSkills}
          onToggleApply={onToggleApplySkill}
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
      {showEnv && onSaveEnvVars && (
        <EnvVarsModal
          envVars={envVars ?? {}}
          onSave={(next) => {
            onSaveEnvVars(next);
            setShowEnv(false);
          }}
          onClose={() => setShowEnv(false)}
          // #750: which item, so the panel can offer a field per variable this
          // item's own tools declared. Only reached when the modal is open, so
          // a closed panel costs nothing.
          slug={slug}
          itemId={investigationId}
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
      {appIcon ? <AppIcon icon={appIcon} slug={slug} color={appColor} size={20} /> : null}
      <div
        data-testid="agent-header-identity"
        // `flex: 1` (basis 0%) let this block collapse to ~21px while the action
        // buttons kept their intrinsic width, so the title read "R…" at EVERY
        // viewport — and the #456 wrap never engaged, because a zero-basis item
        // always "fits" and the row therefore never has to break. A real basis
        // makes the buttons drop to a second row before the title is crushed;
        // shrink stays on so a genuinely tiny panel still degrades gracefully.
        style={{ flexGrow: 1, flexShrink: 1, flexBasis: 160, minWidth: 0 }}
      >
        <div
          title={appTitle}
          style={{
            fontWeight: 600,
            fontSize: "var(--text-body-sm)",
            // Ellipsize on one line — never wrap the App title word-by-word (#456).
            overflow: "hidden",
            whiteSpace: "nowrap",
            textOverflow: "ellipsis",
          }}
        >
          {appTitle}
        </div>
        <div
          style={{
            fontSize: pxToRem(11),
            color: "var(--text-paper-d)",
            overflow: "hidden",
            whiteSpace: "nowrap",
            textOverflow: "ellipsis",
          }}
        >
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
          style={hdrBtn}
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
          style={hdrBtn}
        >
          <Icon name="settings" size={13} /> {t("tools.button")}
        </button>
      )}
      {onSaveEnvVars && (
        <button
          type="button"
          // The item's environment variables, for the tools this workspace runs.
          // Shown only when the parent can persist onto an item, like Tools.
          data-testid="env-button"
          onClick={() => setShowEnv(true)}
          title={t("env.title")}
          aria-label={t("env.title")}
          style={hdrBtn}
        >
          <Icon name="settings" size={13} /> {t("env.button")}
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
        style={hdrBtn}
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
        style={hdrBtn}
      >
        <Icon name="workflow" size={13} /> {t("workflows.button")}
      </button>
      {/* Only where there is a chat to name. Without one the button could only
          ask the server to pick, and it used to pick the item's first — so the
          absent case is drawn as absent, the way #739's gauge is. */}
      {chatId && (
        <button
          type="button"
          // Downloads the `.chat.json` round-trip format (issue #39): re-uploadable
          // to a KB collection, where the BE runs the same insight extraction the
          // promote path does. Goes through the app-scoped route (#95) and validates
          // the response, so a misroute surfaces an error instead of silently saving
          // the SPA shell as `export-chat.html` (#100). Format details live in code.
          onClick={() => {
            setExportError(null);
            downloadChatExport(slug, investigationId, chatId).catch((e) =>
              setExportError(e instanceof Error ? e.message : "匯出失敗"),
            );
          }}
          title="Export this conversation"
          aria-label="Export conversation"
          style={hdrBtn}
        >
          <Icon name="download" size={13} /> Export
        </button>
      )}
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
