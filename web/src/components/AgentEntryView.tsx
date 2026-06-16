/**
 * Shared rendering of an agent-log entry — used by both the RCA AgentPanel and
 * the KB chat so they look identical: attributed user/agent messages, foldable
 * "Show thinking" reasoning, foldable tool-call cards (name(args) · result,
 * live stdout while running), and banners. KB answers may carry citations,
 * rendered as clickable source cards.
 */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import type { Message, MessageCitation } from "../api/types";
import type { AgentEntry, ToolCallView } from "../pages/investigation/agentLog";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { Icon } from "./Icon";
import { RcaMark } from "./RcaMark";
import { UserChip } from "./UserChip";

export function EntryView({
  entry,
  onOpenCitation,
  onReplay,
  onUndo,
}: {
  entry: AgentEntry;
  onOpenCitation?: (c: MessageCitation) => void;
  /** #51 P6: re-run this step (assistant answer / tool decision)
   * against the current model as a diagnostic — provided by surfaces
   * that know the persisted thread position; absent → no affordance. */
  onReplay?: () => void;
  /** #38: undo this user turn and everything after it — provided only
   * for user messages by surfaces that support undo; absent → none. */
  onUndo?: () => void;
}) {
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
    return <ToolCallCard call={entry.call} onOpenCitation={onOpenCitation} onReplay={onReplay} />;
  }
  if (entry.kind === "mention") {
    return <MentionLine by={entry.by} users={entry.users} note={entry.note} />;
  }
  return (
    <MessageBlock
      message={entry.message}
      onOpenCitation={onOpenCitation}
      onReplay={onReplay}
      onUndo={onUndo}
    />
  );
}

/** Subtle per-entry trigger for the replay diagnostic (#51 P6). */
function ReplayButton({ onReplay }: { onReplay: () => void }) {
  return (
    <button
      type="button"
      aria-label="Replay this step with the current AI"
      title="Replay this step with the current AI"
      onClick={(e) => {
        // Inside a <summary>: don't toggle the tool card open/closed.
        e.preventDefault();
        e.stopPropagation();
        onReplay();
      }}
      style={{
        border: "none",
        background: "none",
        padding: 2,
        cursor: "pointer",
        color: "var(--text-paper-d2)",
        display: "inline-flex",
        alignItems: "center",
      }}
    >
      <Icon name="refresh" size={11} />
    </button>
  );
}

/** Per-turn "undo to here" trigger on a user message (#38). */
function UndoButton({ onUndo }: { onUndo: () => void }) {
  return (
    <button
      type="button"
      aria-label="Undo this turn and everything after it"
      title="Undo this turn and everything after it"
      onClick={onUndo}
      style={{
        border: "none",
        background: "none",
        padding: 2,
        cursor: "pointer",
        color: "var(--text-paper-d2)",
        display: "inline-flex",
        alignItems: "center",
      }}
    >
      <Icon name="undo" size={12} />
    </button>
  );
}

function MentionLine({ by, users, note }: { by: string; users: string[]; note: string }) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 6,
        padding: "6px 10px",
        background: "var(--paper-2)",
        borderLeft: "2px solid var(--text-paper-d2)",
        fontSize: 12,
        color: "var(--text-paper-d)",
      }}
    >
      <Icon name="user" size={13} color="var(--text-paper-d)" />
      {by ? <UserChip userId={by} size={18} /> : <span>The agent</span>}
      <span>summoned</span>
      {users.map((u, i) => (
        <span key={u} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <UserChip userId={u} size={18} />
          {i < users.length - 1 && <span>,</span>}
        </span>
      ))}
      {note && <span style={{ color: "var(--text-paper)" }}>— {note}</span>}
    </div>
  );
}

function MessageBlock({
  message,
  onOpenCitation,
  onReplay,
  onUndo,
}: {
  message: Message;
  onOpenCitation?: (c: MessageCitation) => void;
  onReplay?: () => void;
  onUndo?: () => void;
}) {
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
          {onUndo && (
            <>
              <span style={{ flex: 1 }} />
              <UndoButton onUndo={onUndo} />
            </>
          )}
        </div>
        <div
          style={{
            marginLeft: 28,
            marginTop: 4,
            fontSize: 13,
            color: "var(--text-paper)",
            // Preserve the user's newlines/spacing (issue #18) — their message is
            // plain text, not markdown, so render it verbatim.
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
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
          {onReplay && <ReplayButton onReplay={onReplay} />}
        </div>
        {message.reasoning && <ReasoningBlock text={message.reasoning} />}
        <div className="md-body md-compact" style={{ marginLeft: 28, marginTop: 4 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
            {message.content}
          </ReactMarkdown>
        </div>
        {message.citations && message.citations.length > 0 && (
          <div className="kb-cites" style={{ marginLeft: 28 }}>
            <div className="kb-cites__label">Sources</div>
            {message.citations.map((c) => (
              <button
                key={`${c.marker}:${c.document_id}#${c.start}`}
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
  // tool messages fold into ToolCallView during reduce; render unattributed
  // ones (e.g. system messages) plainly.
  return (
    <div style={{ fontSize: 12, color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)" }}>
      {message.content}
    </div>
  );
}

function ReasoningBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  // Follow the reasoning as it streams (same rule as the chat) — bounded so a
  // long chain doesn't shove the answer off-screen.
  const preRef = useStickToBottom<HTMLPreElement>(text);
  return (
    <details
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      style={{ marginLeft: 28, marginTop: 4, fontSize: 12, color: "var(--text-paper-d)" }}
    >
      <summary style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
        <Icon name={open ? "chev_d" : "chev_r"} size={11} />
        Show thinking
      </summary>
      <pre
        ref={preRef}
        style={{
          marginTop: 4,
          padding: 8,
          background: "var(--paper-2)",
          borderRadius: 4,
          whiteSpace: "pre-wrap",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--text-paper-d)",
          maxHeight: 220,
          overflow: "auto",
        }}
      >
        {text}
      </pre>
    </details>
  );
}

function ToolCallCard({
  call,
  onOpenCitation,
  onReplay,
}: {
  call: ToolCallView;
  onOpenCitation?: (c: MessageCitation) => void;
  onReplay?: () => void;
}) {
  // While running, show whatever stdout has streamed so far; once done, the
  // final formatted output supersedes it. Auto-expand a streaming tool.
  const body = call.status === "done" ? call.output : (call.liveOutput ?? call.output);
  const streamingLive = call.status === "running" && !!call.liveOutput;
  // Follow streaming stdout to the bottom unless the user scrolls up.
  const preRef = useStickToBottom<HTMLPreElement>(body);
  return (
    <details
      open={streamingLive}
      style={{
        marginLeft: 28,
        background: "var(--white)",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        padding: "8px 10px",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
      }}
    >
      <summary
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          listStyle: "none",
          color: "var(--text-paper)",
        }}
      >
        {call.status === "done" ? (
          <Icon name="check" size={12} color="var(--ok)" />
        ) : (
          <Icon name="play" size={11} color="var(--accent)" />
        )}
        <span>
          {call.name}({summarizeArgs(call.args)})
        </span>
        {body !== undefined && (
          <span style={{ color: "var(--text-paper-d2)", fontSize: 11 }}>
            · {streamingLive ? "streaming…" : "result"}
          </span>
        )}
        {onReplay && <ReplayButton onReplay={onReplay} />}
      </summary>
      {call.parseError && (
        <div style={{ color: "var(--warn)", fontSize: 11, marginTop: 4 }}>
          retry: {call.parseError}
        </div>
      )}
      {body !== undefined && (
        <pre
          ref={preRef}
          style={{
            color: "var(--text-paper-d)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            margin: "6px 0 0",
            maxHeight: 260,
            overflow: "auto",
          }}
        >
          {body}
        </pre>
      )}
      {call.citations && call.citations.length > 0 && (
        // Reference cards under an ask_knowledge_base tool card — same
        // visual treatment as the assistant-answer Sources block on the
        // KB chat. Clicking opens the source document (when the parent
        // wires `onOpenCitation`); no-op otherwise.
        <div className="kb-cites" style={{ marginTop: 6 }}>
          <div className="kb-cites__label">Sources</div>
          {call.citations.map((c) => (
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
    </details>
  );
}

function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => {
      const s = JSON.stringify(v);
      return `${k}=${s.length > 40 ? `${s.slice(0, 40)}…` : s}`;
    })
    .join(", ");
}
