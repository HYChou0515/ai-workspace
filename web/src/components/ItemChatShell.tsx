import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { type ItemChatSummary } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import type { Suggestion } from "../api/types";
import { workflowApi } from "../api/workflows";
import { useItemChat } from "../hooks/useItemChat";
import { useItemChats } from "../hooks/useItemChats";
import { useDecide, useRun, useWorkflowProfiles } from "../hooks/useWorkflow";
import { AgentPanel } from "../pages/investigation/AgentPanel";
import { ItemChatList } from "./ItemChatList";
import { NewChatPicker } from "./NewChatPicker";

/** What ItemChatShell feeds straight through to each chat's AgentPanel — the
 * App-manifest-derived chat chrome (mirrors the props WorkspaceShell passes the
 * RCA `<AgentPanel>`). The shell adds the multi-chat tab rail + workflow gate. */
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
 * The per-item multi-chat shell (topic-hub §3): a tab rail of the item's chats + a
 * new-chat picker ([Free chat] + the seed profile's workflows), with the active chat
 * rendered below as the full RCA `AgentPanel` (model picker, suggestions, @mention,
 * attach, undo, Cmd-Enter — scoped to the active chat via `useItemChat`). A free chat
 * is created on demand; a workflow launch opens a workflow chat (run-driven) and
 * selects it. A paused workflow chat surfaces a Continue affordance (the human_gate
 * decision) above the panel.
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
  const { chats, isLoading, createFreeChat } = useItemChats(slug, itemId);
  const profilesQ = useWorkflowProfiles(slug);
  const workflows = profilesQ.data?.find((p) => p.name === profile)?.workflows ?? [];
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const autoOpened = useRef(false);

  // Default to the first chat (the default chat lists first) once chats load.
  useEffect(() => {
    if (activeChatId == null && chats.length) setActiveChatId(chats[0].chat_id);
  }, [chats, activeChatId]);

  // A brand-new Hub has no chats yet (the default chat materialises on first
  // use, §3). Open one automatically so the item lands on a usable composer
  // instead of an empty placeholder. Guarded so it fires at most once.
  useEffect(() => {
    if (autoOpened.current || isLoading || chats.length) return;
    autoOpened.current = true;
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
        <ItemChatList chats={chats} activeChatId={activeChatId} onSelect={setActiveChatId} />
        <NewChatPicker workflows={workflows} onFreeChat={onFreeChat} onWorkflow={onWorkflow} />
      </div>
      {active ? (
        <ItemChatPanel
          key={active.chat_id}
          slug={slug}
          itemId={itemId}
          chat={active}
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
          No chat open yet — start one above.
        </p>
      )}
    </div>
  );
}

function ItemChatPanel({
  slug,
  itemId,
  chat,
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
} & AgentChrome) {
  // The active chat drives the full RCA AgentPanel (AgentState shape) — the
  // model picker, suggestions, @mention, attach, undo and Cmd-Enter all work
  // per chat. Injected as a prop so AgentPanel needs no <AgentProvider> here.
  const agent = useItemChat({ slug, itemId, chatId: chat.chat_id });
  // Poll the driving run only for a workflow chat — to surface its human gate.
  const run = useRun(slug, itemId, chat.run_id ?? undefined);
  const decide = useDecide(slug, itemId, chat.run_id ?? "");
  const gate = run.data?.status === "awaiting_human" ? run.data.pending_decision : null;

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
