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
import { useDecide, useRun, useWorkflowProfiles } from "../hooks/useWorkflow";
import { AgentPanel } from "../pages/investigation/AgentPanel";
import { ChatSwitcher } from "./ChatSwitcher";
import { CollectionsButton } from "./CollectionsButton";
import { CollectionsPickerModal } from "./CollectionsPickerModal";
import { ManageChatsModal } from "./ManageChatsModal";
import { NewItemPicker } from "./NewItemPicker";

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
  picker,
  suggestions,
  appTitle,
  appIcon,
  appColor,
  attachedPreset,
  onAttachPreset,
}: {
  slug: string;
  itemId: string;
  profile: string;
} & AgentChrome) {
  const qc = useQueryClient();
  const { chats, isLoading, createFreeChat, renameChat, deleteChat } = useItemChats(slug, itemId);
  const profilesQ = useWorkflowProfiles(slug);
  const workflows = profilesQ.data?.find((p) => p.name === profile)?.workflows ?? [];
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [managing, setManaging] = useState(false);
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
  const onWorkflow = async (workflowId: string) => {
    const { chat_id } = await workflowApi.startRun(slug, itemId, workflowId);
    void qc.invalidateQueries({ queryKey: qk.itemChats(slug, itemId) });
    setActiveChatId(chat_id);
  };

  const active = chats.find((c) => c.chat_id === activeChatId) ?? null;

  return (
    <div
      className="item-chat-shell"
      data-testid="item-chat-shell"
      style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}
    >
      <div
        className="item-chat-shell__bar"
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flex: "0 0 auto",
          padding: "4px 8px",
          borderBottom: "1px solid var(--border, #2a2a2a)",
        }}
      >
        <ChatSwitcher
          chats={chats}
          activeChatId={activeChatId}
          onSelect={setActiveChatId}
          onManage={() => setManaging(true)}
        />
        <NewItemPicker workflows={workflows} onFreeChat={onFreeChat} onWorkflow={onWorkflow} />
        <div style={{ flex: 1 }} />
        <CollectionsButton count={collectionCount} onClick={() => setPickerOpen(true)} />
      </div>
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
      {active ? (
        <ItemChatPanel
          key={active.chat_id}
          slug={slug}
          itemId={itemId}
          chat={active}
          workflows={workflows}
          picker={picker}
          suggestions={suggestions}
          appTitle={appTitle}
          appIcon={appIcon}
          appColor={appColor}
          attachedPreset={attachedPreset}
          onAttachPreset={onAttachPreset}
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
  picker,
  suggestions,
  appTitle,
  appIcon,
  appColor,
  attachedPreset,
  onAttachPreset,
}: {
  slug: string;
  itemId: string;
  chat: ItemChatSummary;
  workflows: WorkflowManifestDTO[];
} & AgentChrome) {
  // The active chat drives the full RCA AgentPanel (AgentState shape) — the
  // model picker, suggestions, @mention, attach, undo and Cmd-Enter all work
  // per chat. Injected as a prop so AgentPanel needs no <AgentProvider> here.
  const agent = useItemChat({ slug, itemId, chatId: chat.chat_id });
  // Poll the driving run only for a workflow chat — to surface its human gate.
  const run = useRun(slug, itemId, chat.run_id ?? undefined);
  const decide = useDecide(slug, itemId, chat.run_id ?? "");
  const gate = run.data?.status === "awaiting_human" ? run.data.pending_decision : null;
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
          style={{ flex: "0 0 auto", display: "flex", gap: 8, alignItems: "center", padding: "6px 8px" }}
        >
          <span>{gate.title}</span>
          {gate.allow.map((choice) => (
            <button
              key={choice}
              type="button"
              onClick={() => decide.mutate({ choice })}
              data-testid={`gate-${choice}`}
            >
              {choice === "approve" ? "Continue" : choice}
            </button>
          ))}
        </div>
      )}

      <AgentPanel
        investigationId={itemId}
        agent={agent}
        fill
        phases={phases}
        picker={picker}
        suggestions={suggestions}
        attachedPreset={attachedPreset}
        onAttachPreset={onAttachPreset}
        appTitle={appTitle}
        appIcon={appIcon}
        appColor={appColor}
      />
    </div>
  );
}
