/**
 * Bottom-panel Terminal — wires `POST /investigations/{id}/exec` to a
 * simple readline-shaped UI. Whitespace-tokenises the command (no
 * shell-quoting v1 — wrap in `sh -c '…'` when you need it).
 */

import { useEffect, useRef, useState } from "react";

import { api } from "../../api";
import type { ExecResult } from "../../api/types";
import { Icon } from "../../components/Icon";

type Entry = {
  prompt: string;
  cmd: string;
  result: ExecResult | { kind: "running" } | { kind: "error"; message: string };
};

export function TerminalPane({ investigationId }: { investigationId: string }) {
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<Entry[]>([]);
  const [historyIdx, setHistoryIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [history]);

  const run = async (line: string) => {
    const cmd = line.trim();
    if (!cmd) return;
    const tokens = cmd.split(/\s+/);
    const entry: Entry = {
      prompt: prompt(investigationId),
      cmd,
      result: { kind: "running" },
    };
    setHistory((h) => [...h, entry]);
    setDraft("");
    setHistoryIdx(null);

    try {
      const result = await api.execShell(investigationId, tokens);
      setHistory((h) =>
        h.map((e) => (e === entry ? { ...e, result } : e)),
      );
    } catch (err) {
      setHistory((h) =>
        h.map((e) =>
          e === entry
            ? {
                ...e,
                result: {
                  kind: "error",
                  message: err instanceof Error ? err.message : String(err),
                },
              }
            : e,
        ),
      );
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void run(draft);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const past = history.filter((h) => h.cmd).map((h) => h.cmd);
      if (past.length === 0) return;
      const next = historyIdx == null ? past.length - 1 : Math.max(0, historyIdx - 1);
      setHistoryIdx(next);
      setDraft(past[next] ?? "");
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      const past = history.filter((h) => h.cmd).map((h) => h.cmd);
      if (historyIdx == null) return;
      const next = historyIdx + 1;
      if (next >= past.length) {
        setHistoryIdx(null);
        setDraft("");
      } else {
        setHistoryIdx(next);
        setDraft(past[next] ?? "");
      }
    } else if (e.key === "l" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      setHistory([]);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: 4,
      }}
    >
      <div
        ref={scrollRef}
        className="scrollable"
        style={{
          flex: 1,
          overflowY: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--text-paper)",
        }}
        onClick={() => inputRef.current?.focus()}
      >
        {history.length === 0 && (
          <div style={{ color: "var(--text-paper-d)" }}>
            Run shell commands in the sandbox. Try <kbd>ls</kbd>,{" "}
            <kbd>echo hi</kbd>, <kbd>cat brief.md</kbd>. ⌘L clears.
          </div>
        )}
        {history.map((e, i) => (
          <EntryView key={i} entry={e} />
        ))}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          borderTop: "1px solid var(--paper-3)",
          paddingTop: 4,
          fontFamily: "var(--font-mono)",
          fontSize: 12,
        }}
      >
        <span style={{ color: "var(--accent)" }}>{prompt(investigationId)}</span>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          spellCheck={false}
          autoFocus
          aria-label="terminal command"
          style={{
            flex: 1,
            border: "none",
            outline: "none",
            background: "transparent",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-paper)",
          }}
        />
      </div>
    </div>
  );
}

function EntryView({ entry }: { entry: Entry }) {
  const isRunning = "kind" in entry.result && entry.result.kind === "running";
  const isError = "kind" in entry.result && entry.result.kind === "error";
  const exit = "exit_code" in entry.result ? entry.result.exit_code : null;

  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: "var(--accent)" }}>{entry.prompt}</span>
        <span>{entry.cmd}</span>
        {exit != null && (
          <span
            style={{
              color: exit === 0 ? "var(--ok)" : "var(--err)",
              fontSize: 10,
            }}
            title={`exit ${exit}`}
          >
            {exit === 0 ? "✓" : `✗ ${exit}`}
          </span>
        )}
        {isRunning && (
          <Icon name="play" size={10} color="var(--text-paper-d2)" />
        )}
      </div>
      {!isRunning && !isError && "stdout" in entry.result && entry.result.stdout && (
        <pre style={preStyle}>{entry.result.stdout}</pre>
      )}
      {!isRunning && !isError && "stderr" in entry.result && entry.result.stderr && (
        <pre style={{ ...preStyle, color: "var(--err)" }}>{entry.result.stderr}</pre>
      )}
      {isError && "message" in entry.result && (
        <pre style={{ ...preStyle, color: "var(--err)" }}>{entry.result.message}</pre>
      )}
    </div>
  );
}

function prompt(investigationId: string): string {
  const tail = investigationId.split(":").pop() ?? investigationId;
  return `${tail.slice(0, 8)}$`;
}

const preStyle: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
};
