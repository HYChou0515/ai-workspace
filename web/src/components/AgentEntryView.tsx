/**
 * Shared rendering of an agent-log entry — used by both the RCA AgentPanel and
 * the KB chat so they look identical: attributed user/agent messages, foldable
 * "Show thinking" reasoning, foldable tool-call cards (name(args) · result,
 * live stdout while running), and banners. KB answers may carry citations,
 * rendered as clickable source cards.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import type { Message, MessageCitation, WithheldSource } from "../api/types";
import type { AgentEntry, StepView, ToolCallView } from "../pages/investigation/agentLog";
import {
  buildByMarker,
  kbCiteAnchor,
  kbCiteUrlTransform,
  renderCitedText,
} from "../renderers/kbCite";
import { remarkKbCitation } from "../renderers/report/remarkKbCitation";
import { extractToolImages } from "../renderers/toolImages";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { useT, type MsgKey } from "../lib/i18n";
import { useUser } from "../hooks/useUsers";
import { formatProvenance } from "../lib/provenance";
import { Icon } from "./Icon";
import { RcaMark } from "./RcaMark";
import { useToolLabel } from "./toolCatalog";
import { UserAvatar, UserChip } from "./UserChip";
import { pxToRem } from "../lib/pxToRem";

// One reference card under an answer / ask_knowledge_base tool card. Shared by
// the assistant-answer Sources block and the tool-card block so they stay in
// lockstep. #254: a citation with provenance also shows its source location
// (page / section / sheet …) as a chip between the filename and the snippet.
function CitationCard({
  c,
  onOpen,
}: {
  c: MessageCitation;
  onOpen?: (c: MessageCitation) => void;
}) {
  const t = useT();
  const loc = formatProvenance(c.provenance, t);
  return (
    <button type="button" className="kb-cite" onClick={() => onOpen?.(c)}>
      <span className="kb-cite__marker">[{c.marker}]</span>
      <span className="kb-cite__body">
        <span className="kb-cite__file">{c.filename}</span>
        {loc && <span className="kb-cite__loc">{loc}</span>}
        <span className="kb-cite__snippet">{c.snippet}</span>
      </span>
      <Icon name="arrow_r" size={12} color="var(--text-paper-d2)" />
    </button>
  );
}

// Permission-disclosure: one "🔒 <name> — request access" chip for a knowledge
// source the answer found relevant but the user may see-exist yet not read. The
// content never travels — only the collection's name + owner, both already
// visible to a read_meta holder. The request button flips to a sent state so a
// second click can't double-fire.
function WithheldChip({
  w,
  onRequestAccess,
}: {
  w: WithheldSource;
  onRequestAccess?: (w: WithheldSource) => void;
}) {
  const t = useT();
  const [requested, setRequested] = useState(false);
  return (
    <div className="kb-withheld">
      <span className="kb-withheld__lock" aria-hidden>
        🔒
      </span>
      <span className="kb-withheld__body">
        <span className="kb-withheld__name">{w.name}</span>
        <span className="kb-withheld__owner">
          {t("entry.withheld.owner")}: {w.owner}
        </span>
      </span>
      {onRequestAccess && (
        <button
          type="button"
          className="btn btn--xs"
          disabled={requested}
          onClick={() => {
            setRequested(true);
            onRequestAccess(w);
          }}
        >
          {t(requested ? "entry.withheld.requested" : "entry.withheld.requestAccess")}
        </button>
      )}
    </div>
  );
}

// #160: present each tool as a behavior, never its raw name(args). A friendly
// label (localized) + the single most meaningful argument in plain text.
// Unmapped tools fall back to a generic label — the raw name never reaches the UI.
const TOOL_LABEL: Record<string, MsgKey> = {
  exec: "tool.exec",
  read_file: "tool.read_file",
  read_image: "tool.read_image",
  write_file: "tool.write_file",
  edit_file: "tool.edit_file",
  delete_file: "tool.delete_file",
  ask_knowledge_base: "tool.ask_knowledge_base",
  kb_search: "tool.kb_search",
  search_wiki: "tool.search_wiki",
  resolve_collection: "tool.resolve_collection",
  lookup_glossary: "tool.lookup_glossary",
  update_context_card: "tool.update_context_card",
  create_context_card: "tool.create_context_card",
  read_new_source: "tool.read_new_source",
  list_sources: "tool.list_sources",
  read_source: "tool.read_source",
  read_skill: "tool.read_skill",
};

// The single argument worth showing per tool (others stay in the expandable body).
const TOOL_ARG: Record<string, string> = {
  exec: "cmd",
  read_file: "path",
  read_image: "path",
  write_file: "path",
  edit_file: "path",
  delete_file: "path",
  read_source: "path",
  search_wiki: "query",
  kb_search: "query",
  lookup_glossary: "query",
  ask_knowledge_base: "question",
  read_skill: "name",
  resolve_collection: "ref",
  update_context_card: "title",
  create_context_card: "title",
};

/** A short, plain-text rendering of a tool's primary argument (empty if none). */
function toolArgHint(name: string, args: Record<string, unknown>): string {
  const key = TOOL_ARG[name];
  if (!key) return "";
  const v = args[key];
  if (v == null) return "";
  const s = Array.isArray(v) ? v.join(" ") : typeof v === "string" ? v : JSON.stringify(v);
  if (!s) return "";
  return s.length > 48 ? `${s.slice(0, 48)}…` : s;
}

export function EntryView({
  entry,
  onOpenCitation,
  onRequestAccess,
  onReplay,
  onUndo,
  onReportWiki,
  fileUrl,
  currentUser,
}: {
  entry: AgentEntry;
  onOpenCitation?: (c: MessageCitation) => void;
  /** Permission-disclosure: request read access to a withheld source (fires the
   * owner notification). Provided by chat surfaces; absent → no request button. */
  onRequestAccess?: (w: WithheldSource) => void;
  /** #51 P6: re-run this step (assistant answer / tool decision)
   * against the current model as a diagnostic — provided by surfaces
   * that know the persisted thread position; absent → no affordance. */
  onReplay?: () => void;
  /** #38: undo this user turn and everything after it — provided only
   * for user messages by surfaces that support undo; absent → none. */
  onUndo?: () => void;
  /** #397: report this assistant answer as a wiki error — provided only for
   * assistant messages by wiki-backed surfaces (KB chat); absent → no button. */
  onReportWiki?: () => void;
  /** #285: resolve a workspace-relative path to a content URL so a tool card
   * can render the charts it wrote inline. Provided by item-scoped surfaces
   * (RCA / Playground AgentPanel); absent on KB chat → no inline images. */
  fileUrl?: (path: string) => string;
  /** #583: the signed-in user's id, so MY messages can sit on the right of a
   * shared thread. Absent → nothing is claimed and everything stays left, which
   * is the correct default for a surface that doesn't know who is watching. */
  currentUser?: string;
}) {
  if (entry.kind === "banner") {
    return (
      <div
        style={{
          padding: "6px 10px",
          background: "var(--accent-soft)",
          borderLeft: "2px solid var(--accent)",
          fontSize: pxToRem(12),
          color: "var(--accent-h)",
        }}
      >
        {entry.text}
      </div>
    );
  }
  if (entry.kind === "tool_call") {
    return (
      <ToolCallCard
        call={entry.call}
        onOpenCitation={onOpenCitation}
        onReplay={onReplay}
        fileUrl={fileUrl}
      />
    );
  }
  if (entry.kind === "mention") {
    return <MentionLine by={entry.by} users={entry.users} note={entry.note} />;
  }
  if (entry.kind === "phase") {
    return <PhaseDivider phase={entry.phase} />;
  }
  if (entry.kind === "step") {
    return <StepLine step={entry.step} />;
  }
  return (
    <MessageBlock
      message={entry.message}
      onOpenCitation={onOpenCitation}
      onRequestAccess={onRequestAccess}
      onReplay={onReplay}
      onUndo={onUndo}
      onReportWiki={onReportWiki}
      currentUser={currentUser}
    />
  );
}

/** Subtle per-entry trigger for the replay diagnostic (#51 P6). */
function ReplayButton({ onReplay }: { onReplay: () => void }) {
  const t = useT();
  return (
    <button
      type="button"
      aria-label={t("entry.replay")}
      title={t("entry.replay")}
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

/** #397: report a wrong answer so the wiki gets corrected (on assistant
 * messages when the chat's collection has a wiki). */
function ReportWikiButton({ onReport }: { onReport: () => void }) {
  const t = useT();
  return (
    <button
      type="button"
      aria-label={t("entry.reportWiki")}
      title={t("entry.reportWiki")}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onReport();
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
      <Icon name="bug" size={11} />
    </button>
  );
}

/** Per-turn "undo to here" trigger on a user message (#38). */
function UndoButton({ onUndo }: { onUndo: () => void }) {
  const t = useT();
  // A bare low-contrast icon reads as decoration (#172). Keep the icon always
  // visible but as an obvious bordered button, and reveal a compact text label
  // on hover/focus so its purpose ("undo this turn onward") is legible without
  // relying on the title tooltip alone.
  const [show, setShow] = useState(false);
  return (
    <button
      type="button"
      aria-label={t("entry.undo")}
      title={t("entry.undo")}
      onClick={onUndo}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
      style={{
        border: "1px solid var(--paper-3)",
        background: show ? "var(--paper-2)" : "var(--white)",
        padding: "2px 6px",
        cursor: "pointer",
        color: "var(--text-paper-d)",
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: pxToRem(11),
        fontFamily: "inherit",
        lineHeight: 1.4,
        borderRadius: "var(--radius-btn)",
      }}
    >
      <Icon name="undo" size={12} />
      {show && <span>{t("entry.undo.label")}</span>}
    </button>
  );
}

function MentionLine({ by, users, note }: { by: string; users: string[]; note: string }) {
  const t = useT();
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
        fontSize: pxToRem(12),
        color: "var(--text-paper-d)",
      }}
    >
      <Icon name="user" size={13} color="var(--text-paper-d)" />
      {by ? <UserChip userId={by} size={18} /> : <span>{t("mention.agent")}</span>}
      <span>{t("mention.summoned")}</span>
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

/** A new workflow phase began — a subtle divider so the feed shows the run
 * moving from one phase to the next (#100 observability). */
function PhaseDivider({ phase }: { phase: string }) {
  return (
    <div
      data-testid="wf-phase"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 10px 2px",
        fontSize: pxToRem(11),
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        color: "var(--text-paper-d)",
      }}
    >
      <span style={{ flex: 1, height: 1, background: "var(--paper-3)" }} />
      <span>{phase}</span>
      <span style={{ flex: 1, height: 1, background: "var(--paper-3)" }} />
    </div>
  );
}

const STEP_GLYPH: Record<StepView["status"], string> = {
  running: "▸",
  passed: "✓",
  failed: "✗",
  skipped: "⤳",
  retrying: "↻",
};

const STEP_COLOR: Record<StepView["status"], string> = {
  running: "var(--text-paper-d)",
  passed: "var(--text-paper-d)",
  failed: "var(--err)",
  skipped: "var(--text-paper-d2)",
  retrying: "var(--warn, var(--text-paper-d))",
};

/** One workflow step's live line — so a deterministic phase (commit: ingest each
 * file) shows movement instead of looking frozen (#100 observability). The line
 * transitions in place as `step.status` advances (running → passed/failed/…). */
function StepLine({ step }: { step: StepView }) {
  const color = STEP_COLOR[step.status];
  return (
    <div data-testid="wf-step" data-status={step.status}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          padding: "3px 10px",
          fontSize: pxToRem(12),
          color: "var(--text-paper-d)",
          fontFamily: "var(--font-mono, monospace)",
        }}
      >
        <span aria-hidden style={{ color }}>
          {STEP_GLYPH[step.status]}
        </span>
        <span style={{ color: "var(--text-paper)" }}>{step.name}</span>
        {step.key && <span style={{ color: "var(--text-paper-d)" }}>· {step.key}</span>}
        {step.reason && <span style={{ color }}>— {step.reason}</span>}
      </div>
      {/* #178: live stdout from a running deterministic step, so a long command
          shows movement instead of looking dead. */}
      {step.liveOutput && (
        <pre
          data-testid="wf-step-output"
          style={{
            margin: "0 10px 4px 24px",
            padding: "4px 8px",
            background: "var(--paper-2)",
            borderRadius: 6,
            fontSize: pxToRem(11),
            maxHeight: 160,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            color: "var(--text-paper-d)",
          }}
        >
          {step.liveOutput}
        </pre>
      )}
    </div>
  );
}

function MessageBlock({
  message,
  onOpenCitation,
  onRequestAccess,
  onReplay,
  onUndo,
  onReportWiki,
  currentUser,
}: {
  message: Message;
  onOpenCitation?: (c: MessageCitation) => void;
  onRequestAccess?: (w: WithheldSource) => void;
  onReplay?: () => void;
  onUndo?: () => void;
  onReportWiki?: () => void;
  currentUser?: string;
}) {
  const t = useT();
  // #583: only a HUMAN message I actually wrote moves to the right. `author` is
  // stamped server-side and arrives over the broadcast (there is no optimistic
  // echo — see useChatSession), so this is stable from the first paint. An
  // author-less message is NOT claimed: in a shared item chat it could be
  // anyone's, and putting someone else's words on my side is worse than leaving
  // every legacy message where it already was.
  // The thread used to print the raw user id and an avatar made of its first two
  // characters, while `useUser`/`UserAvatar` (directory name + photo) were already
  // used for the mention line right below. Resolve it here — unconditionally, so
  // the hook order never depends on the role.
  const author = useUser(message.author ?? "");
  const mine =
    message.role === "user" &&
    currentUser !== undefined &&
    message.author !== undefined &&
    message.author !== null &&
    message.author === currentUser;
  // #221: resolve the answer body's `[n]` markers against this message's
  // citations so each inline marker becomes a clickable pill (same target as
  // the Sources cards below). Empty map ⇒ markers render as muted text.
  const byMarker = useMemo(() => buildByMarker(message.citations ?? []), [message.citations]);
  if (message.role === "user") {
    return (
      <div
        data-testid="message-block"
        data-mine={mine ? "true" : "false"}
        style={
          mine
            ? {
                // `alignSelf` shrinks the block to its text inside the feed's flex
                // column — a full-width block has nowhere to move, so this is what
                // makes the shift exist at all. The cap stops a long paste from
                // spanning the column and landing back where it started.
                alignSelf: "flex-end",
                maxWidth: "72%",
                minWidth: 0,
                // Alignment alone is not enough: a SHORT message reads as
                // right-aligned, but a long one runs back toward the left margin
                // and its leading edge lines up with nothing, so it reads as
                // oddly-indented prose. The fill is what makes the block's
                // boundary visible, and the boundary is the whole signal.
                //
                // Deliberately the LOW-contrast surface token on the page ground,
                // no tail, no accent: your own message must not become the
                // heaviest thing on screen — the agent's answer is what you came
                // to read. `--radius-card` is the radius tool cards already use,
                // so this borrows the existing chrome rather than inventing a
                // second shape language.
                background: "var(--paper-2)",
                borderRadius: "var(--radius-card)",
                padding: "8px 10px",
              }
            : undefined
        }
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            color: "var(--text-paper-d)",
            fontSize: pxToRem(11),
            fontFamily: "var(--font-mono)",
            // Mirror the header so the avatar hugs the same edge as the block.
            // The undo control keeps its spacer and so stays at the far end.
            flexDirection: mine ? "row-reverse" : "row",
          }}
        >
          <UserAvatar userId={message.author ?? ""} size={20} />
          <span>{message.author ? author.name : "user"}</span>
          {onUndo && (
            <>
              <span style={{ flex: 1 }} />
              <UndoButton onUndo={onUndo} />
            </>
          )}
        </div>
        <div
          data-testid="message-body"
          style={{
            // The 28px indent lines the text up under the name; inside my filled
            // block the padding already provides that inset, so no margin. The
            // text itself is NEVER right-aligned — ragged-left multi-line text is
            // markedly harder to read, and the block's visible edge is what says
            // "this one is mine".
            marginLeft: mine ? 0 : 28,
            marginTop: 4,
            fontSize: pxToRem(13),
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
      <div data-testid="message-block" data-mine="false">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            color: "var(--text-paper-d)",
            fontSize: pxToRem(11),
            fontFamily: "var(--font-mono)",
          }}
        >
          <span
            style={{
              width: 20,
              height: 20,
              borderRadius: "var(--radius-chip)",
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
          {onReportWiki && <ReportWikiButton onReport={onReportWiki} />}
        </div>
        {message.reasoning && (
          <ReasoningBlock text={message.reasoning} answered={message.content.trim().length > 0} />
        )}
        <div className="md-body md-compact" style={{ marginLeft: 28, marginTop: 4 }}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath, remarkKbCitation]}
            rehypePlugins={[rehypeKatex]}
            urlTransform={kbCiteUrlTransform}
            components={{
              // #221: remarkKbCitation rewrites `[N]` → `kb-cite:N`; render those
              // as clickable inline pills (opens the cited doc via the same
              // handler as the Sources cards). Non-citation links stay normal.
              a: ({ href, children, ...rest }) => {
                const cite = kbCiteAnchor({ href, children }, byMarker, onOpenCitation);
                if (cite) return cite;
                return (
                  <a href={href} {...rest}>
                    {children}
                  </a>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
        {message.stopped_reason === "repetition" && (
          <RepetitionNotice answered={message.content.trim().length > 0} />
        )}
        {message.citations && message.citations.length > 0 && (
          <div className="kb-cites" style={{ marginLeft: 28 }}>
            <div className="kb-cites__label">{t("entry.sources")}</div>
            {message.citations.map((c) => (
              <CitationCard
                key={`${c.marker}:${c.document_id}#${c.start}`}
                c={c}
                onOpen={onOpenCitation}
              />
            ))}
          </div>
        )}
        {message.withheld && message.withheld.length > 0 && (
          <div className="kb-withheld-list" style={{ marginLeft: 28 }}>
            <div className="kb-cites__label">{t("entry.withheld")}</div>
            {message.withheld.map((w) => (
              <WithheldChip key={w.collection_id} w={w} onRequestAccess={onRequestAccess} />
            ))}
          </div>
        )}
      </div>
    );
  }
  // tool messages fold into ToolCallView during reduce; render unattributed
  // ones (e.g. system messages) plainly.
  return (
    <div
      data-testid="message-block"
      data-mine="false"
      style={{ fontSize: pxToRem(12), color: "var(--text-paper-d2)", fontFamily: "var(--font-mono)" }}
    >
      {message.content}
    </div>
  );
}

// #113: the model degenerated into a repetition loop and the turn was stopped.
// Copy describes the outcome only (no internals) — a distinct line when the
// model looped while thinking and never produced an answer.
function RepetitionNotice({ answered }: { answered: boolean }) {
  const t = useT();
  return (
    <div
      role="note"
      style={{
        marginLeft: 28,
        marginTop: 4,
        fontSize: pxToRem(12),
        color: "var(--text-paper-d2)",
        fontStyle: "italic",
      }}
    >
      {answered ? t("repetition.answered") : t("repetition.thinking")}
    </div>
  );
}

function ReasoningBlock({ text, answered = false }: { text: string; answered?: boolean }) {
  const t = useT();
  // Auto-expand the live thinking so the page isn't blank while the model is
  // mid-reasoning (the wait would otherwise look stuck), then auto-collapse the
  // moment the visible answer starts. The user can still toggle freely after.
  const [open, setOpen] = useState(!answered);
  const wasAnswered = useRef(answered);
  // How long the model thought — measured live (mount ≈ the first reasoning
  // delta; frozen at the answer's first token). Absent on a reloaded thread,
  // where the answer was already present, so the summary is just "已思考".
  const startRef = useRef<number | null>(answered ? null : Date.now());
  const [thinkSec, setThinkSec] = useState<number | null>(null);
  useEffect(() => {
    if (answered && !wasAnswered.current) {
      setOpen(false);
      if (startRef.current != null) {
        setThinkSec(Math.max(0, Math.floor((Date.now() - startRef.current) / 1000)));
      }
    }
    wasAnswered.current = answered;
  }, [answered]);
  // Follow the reasoning as it streams (same rule as the chat) — bounded so a
  // long chain doesn't shove the answer off-screen.
  const preRef = useStickToBottom<HTMLPreElement>(text);
  const summary = answered
    ? thinkSec != null
      ? `${t("reasoning.thought")} ${thinkSec}s`
      : t("reasoning.thought")
    : t("reasoning.thinking");
  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      style={{ marginLeft: 28, marginTop: 4, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}
    >
      <summary style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
        <Icon name={open ? "chev_d" : "chev_r"} size={11} />
        {summary}
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
          fontSize: pxToRem(11),
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
  fileUrl,
}: {
  call: ToolCallView;
  onOpenCitation?: (c: MessageCitation) => void;
  onReplay?: () => void;
  fileUrl?: (path: string) => string;
}) {
  const t = useT();
  // While running, show whatever stdout has streamed so far; once done, the
  // final formatted output supersedes it. Auto-expand a streaming tool.
  const body = call.status === "done" ? call.output : (call.liveOutput ?? call.output);
  const streamingLive = call.status === "running" && !!call.liveOutput;
  // #285: charts the tool wrote, rendered inline (when an item-scoped surface
  // gave us a way to resolve the path). Only after the tool finishes — a
  // half-streamed result has no complete path yet.
  const images = useMemo(
    () => (call.status === "done" && fileUrl ? extractToolImages(body) : []),
    [call.status, fileUrl, body],
  );
  // #221: resolve the body's `[n]` markers (ask_knowledge_base attaches its KB
  // citations here) so each becomes a restrained clickable. Empty while
  // streaming / for tools with no citations ⇒ the body stays plain text.
  const byMarker = useMemo(() => buildByMarker(call.citations ?? []), [call.citations]);
  // Follow streaming stdout to the bottom unless the user scrolls up.
  const preRef = useStickToBottom<HTMLPreElement>(body);
  const labelKey = TOOL_LABEL[call.name];
  // #322: the backend tool catalog gives a clean label for tools the FE i18n map
  // doesn't cover (e.g. package commands), so the raw name no longer leaks. Label
  // precedence: curated i18n > backend catalog > the generic "使用工具" fallback.
  const catalogLabel = useToolLabel()(call.name);
  const label = labelKey ? t(labelKey) : (catalogLabel ?? t("tool.fallback"));
  // We know the tool when either source names it → show its primary arg. Only a
  // truly-unknown tool falls back to surfacing its raw name in the hint slot (#206).
  const hint = labelKey || catalogLabel ? toolArgHint(call.name, call.args) : call.name;
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
        fontSize: pxToRem(12),
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
        <span>{label}</span>
        {hint && (
          <span style={{ color: "var(--text-paper-d)" }}>
            {t("tool.argSep")}
            {hint}
          </span>
        )}
        {body !== undefined && (
          <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>
            · {streamingLive ? t("tool.running") : t("tool.result")}
          </span>
        )}
        {onReplay && <ReplayButton onReplay={onReplay} />}
      </summary>
      {call.parseError && (
        <div style={{ color: "var(--warn)", fontSize: pxToRem(11), marginTop: 4 }}>
          {t("entry.retry")}
          {call.parseError}
        </div>
      )}
      {streamingLive && (
        // A streaming stdout is a work-in-progress, not the answer (#170).
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            marginTop: 6,
            fontSize: pxToRem(11),
            color: "var(--accent)",
          }}
        >
          <Icon name="refresh" size={10} color="var(--accent)" />
          {t("tool.streamingHint")}
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
          {renderCitedText(body, byMarker, onOpenCitation)}
        </pre>
      )}
      {images.length > 0 && fileUrl && (
        // #285: charts the tool wrote, rendered inline. Click to open full size.
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          {images.map((src) => (
            <a key={src} href={fileUrl(src)} target="_blank" rel="noreferrer">
              <img
                src={fileUrl(src)}
                alt={src.split("/").pop() ?? "chart"}
                style={{
                  maxWidth: "100%",
                  maxHeight: 360,
                  borderRadius: 4,
                  border: "1px solid var(--paper-3)",
                  display: "block",
                }}
              />
            </a>
          ))}
        </div>
      )}
      {call.citations && call.citations.length > 0 && (
        // Reference cards under an ask_knowledge_base tool card — same
        // visual treatment as the assistant-answer Sources block on the
        // KB chat. Clicking opens the source document (when the parent
        // wires `onOpenCitation`); no-op otherwise.
        <div className="kb-cites" style={{ marginTop: 6 }}>
          <div className="kb-cites__label">{t("entry.sources")}</div>
          {call.citations.map((c) => (
            <CitationCard key={c.marker} c={c} onOpen={onOpenCitation} />
          ))}
        </div>
      )}
    </details>
  );
}

