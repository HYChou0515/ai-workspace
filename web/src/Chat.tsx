import { useRef, useState } from "react";
import type { AgentEvent } from "./events";
import { streamAgentEvents } from "./events";

type TranscriptItem =
  | { kind: "user"; text: string }
  | { kind: "event"; event: AgentEvent };

interface Props {
  workspaceId: string;
}

export function Chat({ workspaceId }: Props) {
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  async function send() {
    if (!draft.trim() || running) return;
    const content = draft;
    setDraft("");
    setItems((prev) => [...prev, { kind: "user", text: content }]);
    setRunning(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      for await (const ev of streamAgentEvents(workspaceId, content, ac.signal)) {
        setItems((prev) => [...prev, { kind: "event", event: ev }]);
        if (ev.type === "done") break;
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
    <>
      <div className="transcript">
        {items.length === 0 ? (
          <div style={{ color: "#94a3b8" }}>
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
    </>
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
  }
}
