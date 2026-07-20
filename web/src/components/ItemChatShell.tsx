import { pxToRem } from "../lib/pxToRem";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { investigationFileService } from "../api/fileService";
import { type ItemChatSummary } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import type { Suggestion } from "../api/types";
import { workflowApi, type WorkflowManifestDTO } from "../api/workflows";
import { useItemChat } from "../hooks/useItemChat";
import { useItemChats } from "../hooks/useItemChats";
import { useItemCollections } from "../hooks/useItemCollections";
import {
  useCancelRun,
  useConfirmSteer,
  useDecide,
  useRun,
  useSteerRun,
  useWorkflowProfiles,
} from "../hooks/useWorkflow";
import { useWorkspaceWorkflows } from "../hooks/useWorkspaceWorkflows";
import { AgentPanel } from "../pages/investigation/AgentPanel";
import { CardDiffReview } from "./CardDiffReview";
import { SteerConfirmCard } from "./SteerConfirmCard";
import { ChatSwitcher } from "./ChatSwitcher";
import { CollectionsButton } from "./CollectionsButton";
import { CollectionsPickerModal } from "./CollectionsPickerModal";
import { ManageChatsModal } from "./ManageChatsModal";
import { NewItemPicker } from "./NewItemPicker";
import { WorkflowDecisionCard } from "./WorkflowDecisionCard";
import { WorkflowLaunchDialog } from "./WorkflowLaunchDialog";
import { WorkflowLaunchMenu } from "./WorkflowLaunchMenu";
import { WorkflowProgress } from "./WorkflowProgress";

/** What ItemChatShell feeds straight through to each chat's AgentPanel — the
 * App-manifest-derived chat chrome (mirrors the props WorkspaceShell passes the
 * RCA `<AgentPanel>`). The shell adds the multi-chat switcher + workflow gate. */
type AgentChrome = {
  picker: { preset: string; name: string }[];
  suggestions?: Suggestion[];
  appTitle?: string;
  appIcon?: string;
  appColor?: string;
  attachedPreset: string;
  onAttachPreset: (preset: string) => void;
  /** #322: persist this item's per-tool override (`attached_tool_prefs`). Threaded
   * to the AgentPanel header's Tools picker; absent → no picker button. */
  onSaveToolPrefs?: (prefs: Record<string, boolean>) => void;
  /** #380: persist this item's per-skill override (`attached_skill_prefs`). Threaded
   * to the AgentPanel header's Skills picker; absent → its Save is a no-op. */
  onSaveSkillPrefs?: (prefs: Record<string, boolean>) => void;
  /** #198: the folder the composer's attach stages files into (the item's profile's
   * upload_dir; default uploads/). Threaded straight through to the AgentPanel. */
  uploadDir: string;
};

/**
 * The per-item multi-chat shell (topic-hub §3, redesigned in #132): a compact chat
 * switcher dropdown + a single `+ New` picker ([Free chat] + the seed profile's
 * workflows) + a manage-all-chats modal (rename / delete / search), with the active
 * chat rendered below as the full RCA `AgentPanel` (model picker, suggestions,
 * @mention, attach, undo, Cmd-Enter — scoped to the active chat via `useItemChat`).
 * A free chat is created on demand; a workflow launch opens a workflow chat
 * (run-driven) and selects it. A paused workflow chat surfaces a Continue affordance
 * (the human_gate decision) above the panel. The bar also carries the collection-set
 * picker (topic-hub §5, #142) — item-level, shared by every chat + the agent.
 */
export function ItemChatShell({
  slug,
  itemId,
  readOnly = false,
  profile,
  chatSwitcher,
  showCollections,
  picker,
  suggestions,
  appTitle,
  appIcon,
  appColor,
  attachedPreset,
  onAttachPreset,
  onSaveToolPrefs,
  onSaveSkillPrefs,
  uploadDir,
}: {
  slug: string;
  itemId: string;
  /** Permission-disclosure: forwarded to the composer — true disables it (the
   * user can read the thread but lacks `converse`). */
  readOnly?: boolean;
  profile: string;
  /** #200: how prominent the switcher is. "auto" hides it until a 2nd chat
   * exists (single-chat-leaning); "always" surfaces it up front (Topic Hub). */
  chatSwitcher: "auto" | "always";
  /** #200: whether this App manages a collection set (the topic-hub §5 picker).
   * Derived upstream from the manifest's `context_files` containing
   * `collections.json`; off for most Apps (RCA / Playground). */
  showCollections: boolean;
} & AgentChrome) {
  const qc = useQueryClient();
  const { chats, isLoading, createFreeChat, renameChat, deleteChat } = useItemChats(slug, itemId);
  const profilesQ = useWorkflowProfiles(slug);
  const wsWorkflowsQ = useWorkspaceWorkflows(slug, itemId);
  // The launch list = the profile's package workflows PLUS the ones the user
  // co-authored in THIS workspace (#323 `.workflows/`, surfaced for launch by #400),
  // so a just-authored workflow is runnable from the same chat that made it. A
  // workspace def shadows a same-id package one — the run resolver does the same.
  const workflows = useMemo<WorkflowManifestDTO[]>(() => {
    const byId = new Map<string, WorkflowManifestDTO>();
    for (const w of profilesQ.data?.find((p) => p.name === profile)?.workflows ?? [])
      byId.set(w.id, w);
    for (const w of wsWorkflowsQ.data ?? []) byId.set(w.id, { input_json: "", ...w });
    return [...byId.values()];
  }, [profilesQ.data, wsWorkflowsQ.data, profile]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [managing, setManaging] = useState(false);
  // #283: a workflow launch opens the pre-flight dialog first; the real start (which
  // opens a workflow chat) happens only on confirm.
  const [pendingWorkflow, setPendingWorkflow] = useState<string | null>(null);
  const reopening = useRef(false);
  /** Why auto-opening the item's first chat failed, if it did. */
  const [openFailed, setOpenFailed] = useState<string | null>(null);

  // The item's collection set (topic-hub §5, #142) is a workspace file shared by
  // every chat + the agent, so the picker lives at the shell level, not per chat.
  const fileService = useMemo(() => investigationFileService(slug, itemId), [slug, itemId]);
  const collections = useItemCollections(fileService);
  const collectionCount = collections.data?.selectedIds.length ?? 0;
  const [pickerOpen, setPickerOpen] = useState(false);

  // Keep a valid selection: when nothing is active, or the active chat was just
  // deleted, fall back to the most-recent chat (chats are activity-sorted, §132).
  useEffect(() => {
    if (!chats.length) return;
    if (activeChatId == null || !chats.some((c) => c.chat_id === activeChatId)) {
      setActiveChatId(chats[0].chat_id);
    }
  }, [chats, activeChatId]);

  // A Hub with no chats (brand-new, or every chat deleted, §132) auto-opens one so
  // the item lands on a usable composer instead of an empty placeholder. `reopening`
  // suppresses a double-create across the create→refetch gap; it clears only once a
  // chat is actually listed, so a later "delete the last chat" re-arms it.
  useEffect(() => {
    if (chats.length) {
      reopening.current = false;
      return;
    }
    if (isLoading || reopening.current) return;
    reopening.current = true;
    setOpenFailed(null);
    void createFreeChat().then(
      (c) => setActiveChatId(c.chat_id),
      (err: unknown) => {
        // The latch STAYS closed. `createFreeChat` is a fresh closure each
        // render, so this effect re-runs on every render — releasing the latch
        // here would turn a failing create into one attempt per render, i.e. a
        // flood aimed at a server that is already unhappy.
        //
        // Auto-open gets exactly one try; recovery is the explicit Retry below.
        // What was actually broken was that a rejection left NO error and NO way
        // out, so the item sat on the placeholder permanently.
        setOpenFailed(err instanceof Error ? err.message : String(err));
      },
    );
  }, [isLoading, chats.length, createFreeChat]);

  const onFreeChat = async () => {
    setActiveChatId((await createFreeChat()).chat_id);
  };
  const onWorkflow = (workflowId: string) => setPendingWorkflow(workflowId);
  /** Why the last workflow launch failed, if it did. A launch that throws left
   * the dialog closed and nothing else — indistinguishable from a launch that
   * worked but is slow to show up, so the user waits for a run that never
   * started. */
  const [launchFailed, setLaunchFailed] = useState<string | null>(null);

  const confirmWorkflow = async () => {
    if (pendingWorkflow == null) return;
    const workflowId = pendingWorkflow;
    setPendingWorkflow(null);
    setLaunchFailed(null);
    try {
      const { chat_id } = await workflowApi.startRun(slug, itemId, workflowId);
      void qc.invalidateQueries({ queryKey: qk.itemChats(slug, itemId) });
      setActiveChatId(chat_id);
    } catch (err: unknown) {
      setLaunchFailed(err instanceof Error ? err.message : String(err));
    }
  };

  const active = chats.find((c) => c.chat_id === activeChatId) ?? null;

  // #200: the switcher leans single-chat — hidden until a second chat exists,
  // unless the App opts into an always-visible switcher (Topic Hub). The bar as a
  // whole renders only when it would carry something: the switcher, the workflow
  // picker, or the collection set. When it carries nothing (a single-chat-leaning
  // App with one chat, e.g. RCA), the lone "+ New chat" escape moves into the chat
  // header instead so the App reads as a single chat — `onNewChat` below.
  const showSwitcher = chatSwitcher === "always" || chats.length > 1;
  const hasWorkflows = workflows.length > 0;
  const showBar = showSwitcher || hasWorkflows || showCollections;

  return (
    <div
      className="item-chat-shell"
      data-testid="item-chat-shell"
      style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}
    >
      {showBar && (
        <div
          className="item-chat-shell__bar"
          data-testid="item-chat-shell__bar"
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            flex: "0 0 auto",
            padding: "4px 8px",
            borderBottom: "1px solid var(--paper-3)",
          }}
        >
          {showSwitcher && (
            <ChatSwitcher
              chats={chats}
              activeChatId={activeChatId}
              onSelect={setActiveChatId}
              onManage={() => setManaging(true)}
            />
          )}
          <NewItemPicker workflows={workflows} onFreeChat={onFreeChat} onWorkflow={onWorkflow} />
          <div style={{ flex: 1 }} />
          {showCollections && (
            <CollectionsButton count={collectionCount} onClick={() => setPickerOpen(true)} />
          )}
        </div>
      )}
      {managing && (
        <ManageChatsModal
          chats={chats}
          activeChatId={activeChatId}
          onClose={() => setManaging(false)}
          onSelect={setActiveChatId}
          onRename={(id, title) => void renameChat(id, title)}
          onDelete={(id) => void deleteChat(id)}
        />
      )}
      {pickerOpen && (
        <CollectionsPickerModal fileService={fileService} onClose={() => setPickerOpen(false)} />
      )}
      {pendingWorkflow != null && (
        <WorkflowLaunchDialog
          slug={slug}
          itemId={itemId}
          workflowId={pendingWorkflow}
          onConfirm={confirmWorkflow}
          onClose={() => setPendingWorkflow(null)}
        />
      )}
      {launchFailed && (
        <p
          data-testid="launch-failed"
          role="alert"
          style={{ margin: 0, padding: "6px 12px", fontSize: pxToRem(12), color: "var(--err)" }}
        >
          工作流程沒有啟動:{launchFailed}
        </p>
      )}
      {active ? (
        <ItemChatPanel
          key={active.chat_id}
          slug={slug}
          itemId={itemId}
          readOnly={readOnly}
          chat={active}
          workflows={workflows}
          // #200: when the bar is hidden the chat header carries the lone escape
          // hatch; when the bar is shown its "+ New" picker already does, so the
          // header omits it (exactly one create-entry is ever visible).
          onNewChat={showBar ? undefined : onFreeChat}
          picker={picker}
          suggestions={suggestions}
          appTitle={appTitle}
          appIcon={appIcon}
          appColor={appColor}
          attachedPreset={attachedPreset}
          onAttachPreset={onAttachPreset}
          onSaveToolPrefs={onSaveToolPrefs}
          onSaveSkillPrefs={onSaveSkillPrefs}
          uploadDir={uploadDir}
        />
      ) : (
        <div className="item-chat-panel__empty" data-testid="no-chat">
          {openFailed ? (
            <>
              <p>對話開啟失敗：{openFailed}</p>
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                data-testid="retry-open-chat"
                onClick={() => {
                  setOpenFailed(null);
                  reopening.current = true;
                  void createFreeChat().then(
                    (c) => setActiveChatId(c.chat_id),
                    (err: unknown) => {
                      reopening.current = false;
                      setOpenFailed(err instanceof Error ? err.message : String(err));
                    },
                  );
                }}
              >
                重試
              </button>
            </>
          ) : (
            <p>No conversation open yet — start one from the menu above to begin.</p>
          )}
        </div>
      )}
    </div>
  );
}

function ItemChatPanel({
  slug,
  itemId,
  readOnly = false,
  chat,
  workflows,
  onNewChat,
  picker,
  suggestions,
  appTitle,
  appIcon,
  appColor,
  attachedPreset,
  onAttachPreset,
  onSaveToolPrefs,
  onSaveSkillPrefs,
  uploadDir,
}: {
  slug: string;
  itemId: string;
  readOnly?: boolean;
  chat: ItemChatSummary;
  workflows: WorkflowManifestDTO[];
  /** #200: the single-chat-leaning escape hatch — present only when the shell
   * bar is hidden, so the header is the sole place to start a fresh chat. */
  onNewChat?: () => void;
} & AgentChrome) {
  // The active chat drives the full RCA AgentPanel (AgentState shape) — the
  // model picker, suggestions, @mention, attach, undo and Cmd-Enter all work
  // per chat. Injected as a prop so AgentPanel needs no <AgentProvider> here.
  const agent = useItemChat({ slug, itemId, chatId: chat.chat_id });
  // Poll the driving run only for a workflow chat — to surface its human gate.
  const run = useRun(slug, itemId, chat.run_id ?? undefined);
  const decide = useDecide(slug, itemId, chat.run_id ?? "");
  const steer = useSteerRun(slug, itemId, chat.run_id ?? "");
  const confirmSteer = useConfirmSteer(slug, itemId, chat.run_id ?? "");
  const cancel = useCancelRun(slug, itemId);
  const gate = run.data?.status === "awaiting_human" ? run.data.pending_decision : null;
  // #288: a steer plan awaiting confirm — pinned like the gate. The steer card and the
  // gate card are mutually exclusive (the backend sets one pending field, not both).
  const steerPlan = run.data?.pending_steer ?? null;
  // #331: the run's declared phase skeleton — WorkflowProgress merges it with the live
  // per-phase progress for the collapsible bar / detail. A free chat (no run_id) → no
  // panel (run.data is undefined, the panel renders nothing).
  const declared = workflows.find((w) => w.id === run.data?.workflow_id)?.phases ?? [];

  // #343: launch a workflow IN this chat (takeover), offered when the chat has no
  // ACTIVE run — a free chat, or one whose previous run is terminal (relaunch in the
  // same thread). The run reuses this chat, so its agent nodes inherit the prepared
  // history; the returned chat_id is this same chat.
  const qc = useQueryClient();
  const [pendingLaunch, setPendingLaunch] = useState<string | null>(null);
  const runActive =
    !!chat.run_id &&
    (run.data?.status === "pending" ||
      run.data?.status === "running" ||
      run.data?.status === "awaiting_human");
  const canLaunch = !runActive && workflows.length > 0;
  const launchHere = async () => {
    if (pendingLaunch == null) return;
    const workflowId = pendingLaunch;
    setPendingLaunch(null);
    await workflowApi.startRun(slug, itemId, workflowId, chat.chat_id);
    // The chat's run_id now points at the new run — refetch the list + thread so the
    // progress bar / run polling pick it up in place.
    void qc.invalidateQueries({ queryKey: qk.itemChats(slug, itemId) });
    void qc.invalidateQueries({ queryKey: qk.itemChat(slug, itemId, chat.chat_id) });
  };

  return (
    <div
      className="item-chat-panel"
      data-testid="item-chat-panel"
      style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
    >
      {canLaunch && (
        <div
          className="item-chat-panel__launch"
          data-testid="item-chat-panel__launch"
          style={{ flex: "0 0 auto", padding: "4px 8px" }}
        >
          <WorkflowLaunchMenu workflows={workflows} onPick={setPendingLaunch} />
        </div>
      )}
      {pendingLaunch != null && (
        <WorkflowLaunchDialog
          slug={slug}
          itemId={itemId}
          workflowId={pendingLaunch}
          onConfirm={launchHere}
          onClose={() => setPendingLaunch(null)}
        />
      )}
      {/* #331: the run's progress (collapsible bar → #283 detail) sits above the
          decision/steer cards and the feed (I1 甲) — the structural overview the
          retired WorkflowRunPanel used to give, restored for the multi-chat shell. */}
      {chat.run_id && (
        <WorkflowProgress
          run={run.data}
          declaredPhases={declared}
          disconnected={(run.failureCount ?? 0) > 0}
          onStop={() => cancel.mutate(chat.run_id!)}
          stopping={cancel.isPending}
        />
      )}

      {gate && (
        <div
          className="item-chat-panel__gate"
          data-testid="workflow-continue"
          // Pin the decision to the top of the chat (#170): the old gate scrolled
          // away with the feed, so a paused run was easy to miss. The richer
          // WorkflowDecisionCard (summary + revise) replaces the bare buttons.
          style={{ flex: "0 0 auto", position: "sticky", top: 0, zIndex: 2, padding: "6px 8px" }}
        >
          <WorkflowDecisionCard
            decision={gate}
            busy={decide.isPending}
            onDecide={(choice, input) =>
              decide.mutate(input === undefined ? { choice } : { choice, input })
            }
            aux={
              <CardDiffReview
                slug={slug}
                itemId={itemId}
                allow={gate.allow}
                busy={decide.isPending}
                onDecide={(choice, input) =>
                  decide.mutate(input === undefined ? { choice } : { choice, input })
                }
              />
            }
          />
        </div>
      )}

      {steerPlan && (
        <div
          className="item-chat-panel__steer"
          data-testid="workflow-steer"
          // Pin the steer plan to the top like the gate (#288) — it's the same
          // "it's your turn to act" moment, just for a redirect instead of a gate.
          style={{ flex: "0 0 auto", position: "sticky", top: 0, zIndex: 2, padding: "6px 8px" }}
        >
          <SteerConfirmCard
            plan={steerPlan}
            busy={confirmSteer.isPending}
            onConfirm={(approve) => confirmSteer.mutate(approve)}
          />
        </div>
      )}

      <AgentPanel
        investigationId={itemId}
        readOnly={readOnly}
        agent={agent}
        fill
        onNewChat={onNewChat}
        onSteer={chat.run_id ? (text) => steer.mutate(text) : undefined}
        picker={picker}
        suggestions={suggestions}
        attachedPreset={attachedPreset}
        onAttachPreset={onAttachPreset}
        onSaveToolPrefs={onSaveToolPrefs}
        onSaveSkillPrefs={onSaveSkillPrefs}
        appTitle={appTitle}
        appIcon={appIcon}
        appColor={appColor}
        uploadDir={uploadDir}
      />
    </div>
  );
}
