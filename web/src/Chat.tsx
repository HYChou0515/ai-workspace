import { useEffect, useRef, useState } from "react";
import { api, type Message } from "./api";
import type { AgentEvent } from "./events";
import { isTerminal } from "./events";

type TranscriptItem =
  | { kind: "user"; text: string }
  | { kind: "event"; event: AgentEvent };

interface Props {
  workspaceId: string;
  /** Fires when a tool that touched the FS finished. Drives F3 file refresh. */
  onFileMutation?: () => void;
}

function hydrateFromMessages(messages: Message[]): TranscriptItem[] {
  return messages.map<TranscriptItem>((m) => {
    if (m.role === "user") return { kind: "user", text: m.content };
    if (m.role === "tool") {
      return {
        kind: "event",
        event: {
          type: "tool_end",
          call_id: m.tool_call_id ?? "",
          output: m.content,
        },
      };
    }
    return {
      kind: "event",
      event: { type: "message_delta", text: m.content },
    };
  });
}

const FS_MUTATING_TOOLS = new Set([
  "write_file",
  "delete_file",
  "exec",
]);

export function Chat({ workspaceId, onFileMutation }: Props) {
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [running, setRunning] = useState(false);
  const [hydrating, setHydrating] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const toolNamesRef = useRef<Map<string, string>>(new Map());

  // F2 — hydrate conversation history on workspace switch.
  useEffect(() => {
    let cancelled = false;
    setHydrating(true);
    setItems([]);
    api
      .getConversationByWorkspace(workspaceId)
      .then((conv) => {
        if (cancelled) return;
        if (conv) setItems(hydrateFromMessages(conv.messages));
      })
      .catch(() => {
        // hydration failure is non-fatal; show empty transcript
      })
      .finally(() => {
        if (!cancelled) setHydrating(false);
      });
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [workspaceId]);

  async function send() {
    if (!draft.trim() || running) return;
    const content = draft;
    setDraft("");
    setItems((prev) => [...prev, { kind: "user", text: content }]);
    setRunning(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      for await (const ev of api.streamAgentEvents({
        workspaceId,
        content,
        signal: ac.signal,
      })) {
        if (ev.type === "tool_start") {
          toolNamesRef.current.set(ev.call_id, ev.name);
        }
        if (ev.type === "tool_end") {
          const toolName = toolNamesRef.current.get(ev.call_id);
          if (toolName && FS_MUTATING_TOOLS.has(toolName)) {
            onFileMutation?.();
          }
          toolNamesRef.current.delete(ev.call_id);
        }
        setItems((prev) => [...prev, { kind: "event", event: ev }]);
        if (isTerminal(ev)) break;
      }
    } catch (err) {
      setItems((prev) => [
        ...prev,
        { kind: "event", event: { type: "error", message: String(err) } },
      ]);
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <div className="chat">
      <div className="transcript">
        {hydrating ? (
          <div className="ws-status">Loading conversation…</div>
        ) : items.length === 0 ? (
          <div className="ws-status">
            No messages yet. Type below and press <kbd>Cmd/Ctrl+Enter</kbd> to send.
          </div>
        ) : (
          items.map((item, i) => <TranscriptRow key={i} item={item} />)
        )}
      </div>
      <div className="composer">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask the agent to do something…"
          disabled={running}
        />
        <button type="button" onClick={() => void send()} disabled={running || !draft.trim()}>
          {running ? "Running…" : "Send"}
        </button>
      </div>
    </div>
  );
}

function TranscriptRow({ item }: { item: TranscriptItem }) {
  if (item.kind === "user") {
    return <div className="event user">{item.text}</div>;
  }
  const ev = item.event;
  switch (ev.type) {
    case "message_delta":
      return <div className="event message">{ev.text}</div>;
    case "tool_start":
      return (
        <div className="event tool-start">
          ▸ {ev.name}({JSON.stringify(ev.args)})
        </div>
      );
    case "tool_end":
      return <div className="event tool-end">↳ {ev.output}</div>;
    case "error":
      return <div className="event error">error: {ev.message}</div>;
    case "done":
      return <div className="event done">— done —</div>;
    case "run_cancelled":
      return <div className="event cancelled">— cancelled —</div>;
    case "sandbox_killed_idle":
      return (
        <div className="event banner">
          Sandbox went to sleep — next shell command will cold-start.
        </div>
      );
    case "tool_call_parse_error":
      return (
        <div className="event parse-error">
          ✗ tool call parse error ({ev.call_id}): {ev.hint}
        </div>
      );
    case "max_turns_exceeded":
      return (
        <div className="event error">
          max turns exceeded ({ev.turns}) — agent didn't converge
        </div>
      );
  }
}
