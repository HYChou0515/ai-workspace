import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { type ItemChatSummary } from "../api/itemChats";
import { qk } from "../api/queryKeys";
import { workflowApi } from "../api/workflows";
import { useItemChat } from "../hooks/useItemChat";
import { useItemChats } from "../hooks/useItemChats";
import { useDecide, useRun, useWorkflowProfiles } from "../hooks/useWorkflow";
import { EntryView } from "./AgentEntryView";
import { ItemChatList } from "./ItemChatList";
import { NewChatPicker } from "./NewChatPicker";

/**
 * The per-item multi-chat shell (topic-hub §3): a tab rail of the item's chats + a
 * new-chat picker ([Free chat] + the seed profile's workflows), with the active chat
 * rendered below. A free chat is created on demand; a workflow launch opens a
 * workflow chat (run-driven) and selects it. A paused workflow chat surfaces a
 * Continue affordance (the human_gate decision).
 */
export function ItemChatShell({
  slug,
  itemId,
  profile,
}: {
  slug: string;
  itemId: string;
  profile: string;
}) {
  const qc = useQueryClient();
  const { chats, createFreeChat } = useItemChats(slug, itemId);
  const profilesQ = useWorkflowProfiles(slug);
  const workflows = profilesQ.data?.find((p) => p.name === profile)?.workflows ?? [];
  const [activeChatId, setActiveChatId] = useState<string | null>(null);

  // Default to the first chat (the default chat lists first) once chats load.
  useEffect(() => {
    if (activeChatId == null && chats.length) setActiveChatId(chats[0].chat_id);
  }, [chats, activeChatId]);

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
        <ItemChatPanel key={active.chat_id} slug={slug} itemId={itemId} chat={active} />
      ) : (
        <p data-testid="no-chat" style={{ padding: 16 }}>
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
}: {
  slug: string;
  itemId: string;
  chat: ItemChatSummary;
}) {
  const { log, send, cancel } = useItemChat({ slug, itemId, chatId: chat.chat_id });
  const [draft, setDraft] = useState("");
  // Poll the driving run only for a workflow chat — to surface its human gate.
  const run = useRun(slug, itemId, chat.run_id ?? undefined);
  const decide = useDecide(slug, itemId, chat.run_id ?? "");
  const gate = run.data?.status === "awaiting_human" ? run.data.pending_decision : null;

  const submit = () => {
    const text = draft.trim();
    if (!text || log.streaming) return;
    void send(text);
    setDraft("");
  };

  return (
    <div
      className="item-chat-panel"
      data-testid="item-chat-panel"
      style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
    >
      <div className="item-chat-panel__log" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 8 }}>
        {log.entries.map((entry, i) => (
          <EntryView key={i} entry={entry} />
        ))}
        {log.error && (
          <p className="item-chat-panel__error" data-testid="chat-error">
            {log.error}
          </p>
        )}
      </div>

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

      <div
        className="item-chat-panel__composer"
        style={{ flex: "0 0 auto", display: "flex", gap: 8, padding: 8 }}
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={log.streaming}
          aria-label="Message"
          data-testid="chat-composer"
        />
        {log.streaming ? (
          <button type="button" onClick={cancel} data-testid="chat-stop">
            Stop
          </button>
        ) : (
          <button type="button" onClick={submit} disabled={!draft.trim()} data-testid="chat-send">
            Send
          </button>
        )}
      </div>
    </div>
  );
}
