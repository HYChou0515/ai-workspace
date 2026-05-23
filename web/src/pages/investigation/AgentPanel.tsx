/**
 * Right-column agent panel. Hydrates from /conversation, streams replies
 * via POST /investigations/{id}/messages, renders the design's mix of
 * user / agent / tool-call entries, with suggestion chips + composer.
 */

import { useRef, useState } from "react";

import { api } from "../../api";
import { Icon } from "../../components/Icon";
import { RcaMark } from "../../components/RcaMark";
import { useAgent } from "../../hooks/useAgent";
import type { AgentEntry, ToolCallView } from "./agentLog";
import type { Message } from "../../api/types";

// Fallback only — the real quick-prompts come from the attached AgentConfig
// (BE), passed in via `suggestions`.
const DEFAULT_SUGGESTIONS = ["Show SPC analysis", "Run Pareto", "Sketch a fishbone"];

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
  const chips = suggestions && suggestions.length > 0 ? suggestions : DEFAULT_SUGGESTIONS;
  const { log, send, cancel } = useAgent();
  const [draft, setDraft] = useState("");
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
    if (!text || log.streaming) return;
    setDraft("");
    void send(text);
  };

  const onChip = (label: string) => {
    if (log.streaming) return;
    void send(label);
  };

  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
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
        <textarea
          ref={composerRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onComposerKeyDown}
          placeholder="Ask the agent…"
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
            <button
              type="submit"
              disabled={!draft.trim()}
              style={{
                padding: "6px 14px",
                borderRadius: "var(--radius-btn)",
                background: draft.trim() ? "var(--accent)" : "var(--paper-3)",
                color: draft.trim() ? "var(--white)" : "var(--text-paper-d)",
                fontSize: 12,
                fontWeight: 500,
              }}
            >
              Send
            </button>
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

function EntryView({ entry }: { entry: AgentEntry }) {
  if (entry.kind === "banner") {
    return (
      <div
        style={{
          padding: "6px 10px",
          background: "var(--accent-soft)",
          borderLeft: "2px solid var(--accent)",
          fontSize: 12,
          color: "var(--accent-h)",
        }}
      >
        {entry.text}
      </div>
    );
  }
  if (entry.kind === "tool_call") {
    return <ToolCallCard call={entry.call} />;
  }
  return <MessageBlock message={entry.message} />;
}

function MessageBlock({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            color: "var(--text-paper-d)",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
          }}
        >
          <span
            style={{
              width: 20,
              height: 20,
              borderRadius: "50%",
              background: "var(--paper-2)",
              border: "1px solid var(--paper-3)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 10,
              fontWeight: 600,
            }}
          >
            {(message.author ?? "U").slice(0, 2).toUpperCase()}
          </span>
          <span>{message.author ?? "user"}</span>
        </div>
        <div
          style={{ marginLeft: 28, marginTop: 4, fontSize: 13, color: "var(--text-paper)" }}
        >
          {message.content}
        </div>
      </div>
    );
  }
  if (message.role === "assistant") {
    return (
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            color: "var(--text-paper-d)",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
          }}
        >
          <span
            style={{
              width: 20,
              height: 20,
              borderRadius: 3,
              background: "var(--ink)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <RcaMark size={14} color="var(--text-dark)" dot="var(--accent)" />
          </span>
          <span>{message.author ?? "Agent"}</span>
        </div>
        {message.reasoning && <ReasoningBlock text={message.reasoning} />}
        <div
          style={{
            marginLeft: 28,
            marginTop: 4,
            fontSize: 13,
            color: "var(--text-paper)",
            whiteSpace: "pre-wrap",
          }}
        >
          {message.content}
        </div>
      </div>
    );
  }
  // tool messages get folded into ToolCallView during reduce; render
  // unattributed ones (e.g. system messages) plainly.
  return (
    <div style={{ fontSize: 12, color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)" }}>
      {message.content}
    </div>
  );
}

function ReasoningBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      style={{
        marginLeft: 28,
        marginTop: 4,
        fontSize: 12,
        color: "var(--text-paper-d)",
      }}
    >
      <summary style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
        <Icon name={open ? "chev_d" : "chev_r"} size={11} />
        Show thinking
      </summary>
      <pre
        style={{
          marginTop: 4,
          padding: 8,
          background: "var(--paper-2)",
          borderRadius: 4,
          whiteSpace: "pre-wrap",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--text-paper-d)",
        }}
      >
        {text}
      </pre>
    </details>
  );
}

function ToolCallCard({ call }: { call: ToolCallView }) {
  return (
    <div
      style={{
        marginLeft: 28,
        background: "var(--white)",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        padding: "8px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {call.status === "done" ? (
          <Icon name="check" size={12} color="var(--ok)" />
        ) : (
          <Icon name="play" size={11} color="var(--accent)" />
        )}
        <span style={{ color: "var(--text-paper)" }}>
          {call.name}({summarizeArgs(call.args)})
        </span>
      </div>
      {call.parseError && (
        <div style={{ color: "var(--warn)", fontSize: 11 }}>
          retry: {call.parseError}
        </div>
      )}
      {call.output !== undefined && (
        <div
          style={{
            color: "var(--text-paper-d)",
            whiteSpace: "pre-wrap",
            maxHeight: 96,
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          → {call.output}
        </div>
      )}
    </div>
  );
}

function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 32)}`)
    .join(", ");
}
