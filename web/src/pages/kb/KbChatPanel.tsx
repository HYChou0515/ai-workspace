/**
 * KbChatPanel — the reusable KB conversation core (no outer chrome). Renders
 * the agent log with the SAME components as the RCA agent (foldable reasoning,
 * tool-call cards, live token metrics) so the two chats look identical; adds a
 * collection picker for a fresh thread + suggestion chips + the composer.
 */

import { useEffect, useMemo, useState } from "react";

import { kbApi, type KbApi, type KbCitation, type KbCollection } from "../../api/kb";
import { EntryView } from "../../components/AgentEntryView";
import { Icon } from "../../components/Icon";
import { useKbChat } from "../../hooks/useKbChat";
import { formatMetrics } from "../investigation/agentLog";

export function KbChatPanel({
  chatId = null,
  onOpenCitation,
  onChatCreated,
  client = kbApi,
}: {
  chatId?: string | null;
  onOpenCitation?: (c: KbCitation) => void;
  onChatCreated?: (chatId: string) => void;
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
    client.getAgentConfig().then((cfg) => on && setSuggestions(cfg.suggestions));
    return () => {
      on = false;
    };
  }, [client]);

  const collectionIds = useMemo(() => [...selected], [selected]);
  const { log, send } = useKbChat({ collectionIds, chatId, client, onChatCreated });

  const submit = (text: string) => {
    const t = text.trim();
    if (!t || log.streaming) return;
    setDraft("");
    void send(t);
  };

  const empty = log.entries.length === 0;
  const showPicker = chatId == null && empty && collections.length > 0;

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
        {empty && (
          <p className="kb-drawer__hello">
            Hi — ask me anything across your knowledge base. I'll cite the sources.
          </p>
        )}
        {log.entries.map((entry, i) => (
          <EntryView key={i} entry={entry} onOpenCitation={onOpenCitation} />
        ))}
        {log.streaming && !log.metrics && <div className="kb-drawer__searching">working…</div>}
        {log.metrics && <div className="kb-metrics">{formatMetrics(log.metrics)}</div>}
        {log.error && <div className="kb-drawer__error">{log.error}</div>}
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
        {empty && suggestions.length > 0 && (
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
              disabled={log.streaming || !draft.trim()}
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
