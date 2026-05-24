/**
 * KbChatPanel — the reusable KB conversation core (no outer chrome): collection
 * picker for a fresh thread, streamed messages with citation cards + "searched"
 * lines, suggestions, and the composer. Wrapped by the fast-chat drawer
 * (slide-in chrome) and by the full-page KbChatView (page chrome).
 */

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbCitation, type KbChatMessage, type KbCollection } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { useKbChat } from "../../hooks/useKbChat";

export function KbChatPanel({
  chatId = null,
  onOpenCitation,
  client = kbApi,
}: {
  /** Continue an existing thread; null starts a fresh one on first send. */
  chatId?: string | null;
  onOpenCitation?: (c: KbCitation) => void;
  client?: KbApi;
}) {
  const [collections, setCollections] = useState<KbCollection[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    let on = true;
    client.listCollections().then((cs) => {
      if (!on) return;
      setCollections(cs);
      setSelected(new Set(cs.map((c) => c.resource_id))); // default: search all
    });
    // Quick-prompt chips come from the KB agent config, not the FE.
    client.getAgentConfig().then((cfg) => on && setSuggestions(cfg.suggestions));
    return () => {
      on = false;
    };
  }, [client]);

  const collectionIds = useMemo(() => [...selected], [selected]);
  const { messages, streaming, error, send } = useKbChat({ collectionIds, chatId, client });

  const submit = (text: string) => {
    const t = text.trim();
    if (!t || streaming) return;
    setDraft("");
    void send(t);
  };

  // The scope picker only applies to a NOT-yet-started thread (the backend pins
  // collections onto the chat at creation; an existing thread's scope is fixed).
  const showPicker = chatId == null && messages.length === 0 && collections.length > 0;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="kb-chatpanel">
      <div className="kb-chatpanel__body">
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
        {showPicker && (
          <div className="kb-picker" aria-label="Collections to search">
            <span className="kb-picker__label">Search in</span>
            {collections.map((c) => {
              const on = selected.has(c.resource_id);
              return (
                <button
                  key={c.resource_id}
                  type="button"
                  className={`kb-chip${on ? " is-on" : ""}`}
                  aria-pressed={on}
                  onClick={() => toggle(c.resource_id)}
                >
                  {on && <Icon name="check" size={10} />}
                  {c.name}
                </button>
              );
            })}
          </div>
        )}
        {messages.length === 0 && suggestions.length > 0 && (
          <div className="kb-suggestions">
            {suggestions.map((s) => (
              <button key={s} type="button" className="kb-suggestion" onClick={() => submit(s)}>
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
                submit(draft);
              }
            }}
          />
          <div className="kb-composer__row">
            <span className="kb-composer__hint">⌘↵ to send</span>
            <button
              type="button"
              className="kb-btn kb-btn--primary"
              disabled={streaming || !draft.trim()}
              onClick={() => submit(draft)}
            >
              <Icon name="arrow_r" size={13} /> Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

type SearchResult = { n: string; file: string; text: string };

/** Parse kb_search's "[1] file: text\n\n[2] …" output into result rows so the
 * retrieved passages show as distinct search results, not the AI's answer. */
function parseSearchResults(output: string): SearchResult[] {
  const out: SearchResult[] = [];
  for (const chunk of output.split(/\n\n+/)) {
    const m = chunk.match(/^\[(\d+)\]\s+([^:]+):\s*([\s\S]*)$/);
    if (m) out.push({ n: m[1], file: m[2], text: m[3].trim() });
  }
  return out;
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
    const query = typeof message.tool_args?.query === "string" ? message.tool_args.query : null;
    const results = parseSearchResults(message.content);
    return (
      <div className="kb-results">
        <div className="kb-results__head">
          <Icon name="search" size={12} color="var(--text-paper-d2)" />
          Searched the knowledge base{query ? ` · “${query}”` : ""}
        </div>
        {results.length > 0 ? (
          <ul className="kb-results__list">
            {results.map((r, i) => (
              <li key={i} className="kb-results__item">
                <span className="kb-cite__marker">[{r.n}]</span>
                <div className="kb-cite__body">
                  <span className="kb-cite__file">{r.file}</span>
                  <span className="kb-results__text">{r.text}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="kb-results__empty">{message.content || "No matching passages."}</div>
        )}
      </div>
    );
  }
  return (
    <div className="kb-msg kb-msg--agent">
      <div className="kb-msg__who">
        <span className="kb-msg__mark">
          <Icon name="sparkle" size={12} color="var(--accent)" />
        </span>
        Answer
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
