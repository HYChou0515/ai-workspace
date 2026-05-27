/**
 * Right-column agent panel. Hydrates from /conversation, streams replies
 * via POST /investigations/{id}/messages, renders the design's mix of
 * user / agent / tool-call entries, with suggestion chips + composer.
 */

import { useRef, useState } from "react";

import { api } from "../../api";
import { EntryView } from "../../components/AgentEntryView";
import { Icon } from "../../components/Icon";
import { Popover } from "../../components/Popover";
import { RcaMark } from "../../components/RcaMark";
import { UserChip } from "../../components/UserChip";
import { UserPicker } from "../../components/UserPicker";
import { useAgent } from "../../hooks/useAgent";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useStickToBottom } from "../../hooks/useStickToBottom";
import { formatMetrics, isToolRunning } from "./agentLog";

const TEXT_EXTENSIONS = new Set([
  ".md",
  ".markdown",
  ".txt",
  ".csv",
  ".tsv",
  ".json",
  ".log",
  ".py",
  ".yaml",
  ".yml",
  ".xml",
  ".html",
]);

function isTextFile(name: string): boolean {
  const idx = name.lastIndexOf(".");
  if (idx === -1) return false;
  return TEXT_EXTENSIONS.has(name.slice(idx).toLowerCase());
}

export function AgentPanel({
  investigationId,
  width = 380,
  suggestions,
}: {
  investigationId: string;
  width?: number;
  /** Quick-prompt chips from the attached AgentConfig (BE). */
  suggestions?: string[];
}) {
  // Quick-prompt chips come ONLY from the attached AgentConfig (BE) — the FE
  // never invents its own. No config suggestions → no chip row.
  const chips = suggestions ?? [];
  const me = useCurrentUser();
  const { log, send, mention, cancel } = useAgent();
  const chatScrollRef = useStickToBottom<HTMLDivElement>(log);
  const [draft, setDraft] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  const [attaching, setAttaching] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const onAttach = async (file: File) => {
    if (!isTextFile(file.name)) {
      alert(
        `Only text files (.md/.txt/.csv/.json/.py/etc.) can be attached in v1. Got: ${file.name}`,
      );
      return;
    }
    if (file.size > 256 * 1024) {
      alert(`File too large (${(file.size / 1024).toFixed(0)} KB) — v1 cap is 256 KB.`);
      return;
    }
    setAttaching(true);
    try {
      const text = await file.text();
      const path = `/uploads/${file.name}`;
      await api.writeFile(investigationId, path, text);
      const ref = `I've attached \`${path}\` — please review it.\n\n`;
      setDraft((d) => (d ? `${ref}${d}` : ref));
      composerRef.current?.focus();
    } catch (err) {
      console.error("attach failed", err);
      alert(`Attach failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setAttaching(false);
    }
  };

  const submit = () => {
    const text = draft.trim();
    if (log.streaming) return;
    // A message that @-mentions people is a summon — it notifies them and does
    // NOT run the agent (the draft becomes the note).
    if (mentions.length > 0) {
      void mention(mentions, text);
      setMentions([]);
      setDraft("");
      return;
    }
    if (!text) return;
    setDraft("");
    void send(text);
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
        width,
        flexShrink: 0,
        background: "var(--paper)",
        borderLeft: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
      }}
    >
      <AgentHeader streaming={log.streaming} />
      <ProgressBar streaming={log.streaming} />

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
          <div style={{ color: "var(--text-paper-d)", fontSize: 13 }}>
            Ask the agent anything — it can read evidence, run notebooks,
            and draft 5-Why / 8D entries.
          </div>
        )}
        {log.entries.map((e, i) => (
          <EntryView key={i} entry={e} />
        ))}
        {log.streaming && log.metrics && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--accent)",
              padding: "2px 0 0 28px",
            }}
          >
            {formatMetrics(log.metrics, isToolRunning(log))}
          </div>
        )}
        {log.error && (
          <div
            style={{
              padding: 8,
              border: "1px solid var(--err)",
              borderRadius: "var(--radius-card)",
              color: "var(--err)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
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
            key={s}
            type="button"
            onClick={() => onChip(s)}
            disabled={log.streaming}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "4px 10px",
              borderRadius: 999,
              border: "1px solid var(--paper-3)",
              background: "var(--white)",
              fontSize: 12,
              color: "var(--text-paper)",
              cursor: log.streaming ? "not-allowed" : "pointer",
              opacity: log.streaming ? 0.5 : 1,
            }}
          >
            <Icon name="sparkle" size={12} color="var(--accent)" />
            {s}
          </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        style={{
          padding: 12,
          borderTop: "1px solid var(--paper-3)",
          background: "var(--white)",
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {mentions.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
            <span style={{ fontSize: 11, color: "var(--text-paper-d)" }}>Summon:</span>
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
                  fontSize: 12,
                }}
              >
                <UserChip userId={id} size={16} />
                <Icon name="x" size={11} />
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={composerRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onComposerKeyDown}
          placeholder={mentions.length > 0 ? "Add a note (optional)…" : "Ask the agent…"}
          rows={3}
          style={{
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            padding: 8,
            fontSize: 13,
            resize: "vertical",
            outline: "none",
            fontFamily: "var(--font-body)",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".md,.markdown,.txt,.csv,.tsv,.json,.log,.py,.yaml,.yml,.xml,.html"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void onAttach(f);
            }}
            style={{ display: "none" }}
          />
          <Popover
            trigger={({ onClick }) => (
              <button
                type="button"
                onClick={onClick}
                title="@ mention someone to come look (notifies them — no agent run)"
                style={{ color: "var(--text-paper-d)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}
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
            title="Attach a text file (≤256 KB)"
            style={{
              color: "var(--text-paper-d)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: 12,
            }}
          >
            <Icon name="plus" size={14} />
            {attaching ? "uploading…" : "attach"}
          </button>
          <span style={{ flex: 1 }} />
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--text-paper-d2)",
            }}
          >
            ⌘↵
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
                fontSize: 12,
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
                    fontSize: 12,
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
    </aside>
  );
}

function AgentHeader({ streaming }: { streaming: boolean }) {
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
      <RcaMark size={20} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>RCA Agent</div>
        <div style={{ fontSize: 11, color: "var(--text-paper-d)" }}>
          {streaming ? "investigating · live" : "ready"}
        </div>
      </div>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: "2px 8px",
          borderRadius: "var(--radius-chip)",
          background: streaming ? "var(--accent)" : "var(--paper-2)",
          color: streaming ? "var(--white)" : "var(--text-paper-d)",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
        }}
      >
        <Icon
          name="sparkle"
          size={10}
          color={streaming ? "var(--white)" : "var(--text-paper-d)"}
        />
        {streaming ? "running" : "idle"}
      </span>
    </header>
  );
}

function ProgressBar({ streaming }: { streaming: boolean }) {
  return (
    <div
      style={{
        padding: "8px 14px",
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", gap: 4 }}>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            style={{
              flex: 1,
              height: 4,
              borderRadius: 2,
              background: streaming
                ? i < 4
                  ? "var(--ok)"
                  : i === 4
                    ? "var(--accent)"
                    : "var(--paper-3)"
                : "var(--paper-3)",
            }}
          />
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-paper-d)" }}>
        {streaming ? "step 4 · finding correlations" : "no active run"}
      </div>
    </div>
  );
}
