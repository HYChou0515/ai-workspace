import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { investigationFileService } from "../api/fileService";
import { type ItemChatSummary } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import type { Suggestion } from "../api/types";
import { phaseView, workflowApi, type WorkflowManifestDTO } from "../api/workflows";
import { useItemChat } from "../hooks/useItemChat";
import { useItemChats } from "../hooks/useItemChats";
import { useItemCollections } from "../hooks/useItemCollections";
import {
  useConfirmSteer,
  useDecide,
  useRun,
  useSteerRun,
  useWorkflowProfiles,
} from "../hooks/useWorkflow";
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
  uploadDir,
}: {
  slug: string;
  itemId: string;
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
  const workflows = profilesQ.data?.find((p) => p.name === profile)?.workflows ?? [];
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [managing, setManaging] = useState(false);
  // #283: a workflow launch opens the pre-flight dialog first; the real start (which
  // opens a workflow chat) happens only on confirm.
  const [pendingWorkflow, setPendingWorkflow] = useState<string | null>(null);
  const reopening = useRef(false);

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
    void createFreeChat().then((c) => setActiveChatId(c.chat_id));
  }, [isLoading, chats.length, createFreeChat]);

  const onFreeChat = async () => {
    setActiveChatId((await createFreeChat()).chat_id);
  };
  const onWorkflow = (workflowId: string) => setPendingWorkflow(workflowId);
  const confirmWorkflow = async () => {
    if (pendingWorkflow == null) return;
    const workflowId = pendingWorkflow;
    setPendingWorkflow(null);
    const { chat_id } = await workflowApi.startRun(slug, itemId, workflowId);
    void qc.invalidateQueries({ queryKey: qk.itemChats(slug, itemId) });
    setActiveChatId(chat_id);
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
            borderBottom: "1px solid var(--border, #2a2a2a)",
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
      {active ? (
        <ItemChatPanel
          key={active.chat_id}
          slug={slug}
          itemId={itemId}
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
          uploadDir={uploadDir}
        />
      ) : (
        <p className="item-chat-panel__empty" data-testid="no-chat">
          No conversation open yet — start one from the menu above to begin.
        </p>
      )}
    </div>
  );
}

function ItemChatPanel({
  slug,
  itemId,
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
  uploadDir,
}: {
  slug: string;
  itemId: string;
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
  const gate = run.data?.status === "awaiting_human" ? run.data.pending_decision : null;
  // #288: a steer plan awaiting confirm — pinned like the gate. The steer card and the
  // gate card are mutually exclusive (the backend sets one pending field, not both).
  const steerPlan = run.data?.pending_steer ?? null;
  // The real linear step bar: the run's workflow declares the phase skeleton,
  // merged with its live per-phase progress. A free chat (no run_id) → no bar.
  const declared = workflows.find((w) => w.id === run.data?.workflow_id)?.phases ?? [];
  const phases = chat.run_id ? phaseView(declared, run.data) : undefined;

  return (
    <div
      className="item-chat-panel"
      data-testid="item-chat-panel"
      style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
    >
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
        agent={agent}
        fill
        phases={phases}
        onNewChat={onNewChat}
        onSteer={chat.run_id ? (text) => steer.mutate(text) : undefined}
        picker={picker}
        suggestions={suggestions}
        attachedPreset={attachedPreset}
        onAttachPreset={onAttachPreset}
        appTitle={appTitle}
        appIcon={appIcon}
        appColor={appColor}
        uploadDir={uploadDir}
      />
    </div>
  );
}
