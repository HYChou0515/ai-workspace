/**
 * "Ask agent" drawer — slides in from the right, chats with the KB agent.
 * Streams the answer live, renders it as markdown, and shows the resolved
 * [n] citations as clickable source cards. Aesthetic from the design handoff
 * (sparkle mark on ink, accent composer) over the real backend via useKbChat.
 */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { KbApi, KbCitation, KbChatMessage } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { useKbChat } from "../../hooks/useKbChat";

const SUGGESTIONS = [
  "What does the spec say about void-rate acceptance?",
  "Has reflow zone-3 drift been seen before?",
  "Summarize our wirebond pull-strength findings",
];

export function AskAgentDrawer({
  open,
  onClose,
  collectionIds,
  chatId = null,
  onOpenCitation,
  onManage,
  client,
}: {
  open: boolean;
  onClose: () => void;
  collectionIds: string[];
  /** Continue an existing thread; null starts a fresh one on first send. */
  chatId?: string | null;
  onOpenCitation?: (c: KbCitation) => void;
  onManage?: () => void;
  client?: KbApi;
}) {
  const { messages, streaming, error, send } = useKbChat({ collectionIds, chatId, client });
  const [draft, setDraft] = useState("");

  if (!open) return null;

  const submit = () => {
    const text = draft.trim();
    if (!text || streaming) return;
    setDraft("");
    void send(text);
  };

  return (
    <>
      <div onClick={onClose} className="kb-drawer-backdrop" aria-hidden />
      <aside className="kb-drawer" role="dialog" aria-label="Ask the knowledge base">
        <header className="kb-drawer__head">
          <div className="kb-drawer__mark">
            <Icon name="sparkle" size={16} color="var(--accent)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="kb-drawer__title">Ask the knowledge base</div>
            <button type="button" className="kb-drawer__manage" onClick={onManage}>
              manage sources
            </button>
          </div>
          <button type="button" className="kb-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>

        <div className="kb-drawer__body">
          {messages.length === 0 && (
            <p className="kb-drawer__hello">
              Hi — ask me anything across your knowledge base. I'll cite the sources.
            </p>
          )}
          {messages.map((m, i) => (
            <Message key={i} message={m} onOpenCitation={onOpenCitation} />
          ))}
          {streaming && <div className="kb-drawer__searching">searching…</div>}
          {error && <div className="kb-drawer__error">{error}</div>}
        </div>

        <div className="kb-drawer__foot">
          {messages.length === 0 && (
            <div className="kb-suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="kb-suggestion"
                  onClick={() => {
                    setDraft("");
                    void send(s);
                  }}
                >
                  <Icon name="sparkle" size={11} color="var(--accent)" />
                  {s}
                </button>
              ))}
            </div>
          )}
          <div className="kb-composer">
            <textarea
              className="kb-composer__input"
              rows={2}
              placeholder="Ask the knowledge base…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || !e.shiftKey)) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <div className="kb-composer__row">
              <span className="kb-composer__hint">⌘↵ to send</span>
              <button
                type="button"
                className="kb-btn kb-btn--primary"
                disabled={streaming || !draft.trim()}
                onClick={submit}
              >
                <Icon name="arrow_r" size={13} /> Send
              </button>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}

function Message({
  message,
  onOpenCitation,
}: {
  message: KbChatMessage;
  onOpenCitation?: (c: KbCitation) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="kb-msg kb-msg--user">
        <div className="kb-msg__who">You</div>
        <div className="kb-msg__text">{message.content}</div>
      </div>
    );
  }
  if (message.role === "tool") {
    const query =
      typeof message.tool_args?.query === "string" ? message.tool_args.query : null;
    return (
      <div className="kb-tool">
        <Icon name="search" size={12} color="var(--text-paper-d2)" />
        Searched the knowledge base{query ? `: "${query}"` : ""}
      </div>
    );
  }
  return (
    <div className="kb-msg kb-msg--agent">
      <div className="kb-msg__who">
        <span className="kb-msg__mark">
          <Icon name="sparkle" size={12} color="var(--accent)" />
        </span>
        KB Agent
      </div>
      <div className="kb-msg__text md-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>
      {message.citations.length > 0 && (
        <div className="kb-cites">
          <div className="kb-cites__label">Sources</div>
          {message.citations.map((c) => (
            <button
              key={c.marker}
              type="button"
              className="kb-cite"
              onClick={() => onOpenCitation?.(c)}
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
      )}
    </div>
  );
}
