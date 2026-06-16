/**
 * F8 — notebook viewer. Parses .ipynb client-side, renders cell list with
 * run gutter / cell card / output. Run a cell → SSE stream of CellEvent.
 * On cell_done, persist the updated notebook JSON back to the file store.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { api } from "../../api";
import { CellEditor } from "../../components/CellEditor";
import { Icon } from "../../components/Icon";
import { useOptionalAgent } from "../../hooks/useAgent";
import { useFileBuffer } from "../../hooks/fileBuffer";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { onRunAll } from "../../lib/editorEvents";
import { CellOutput } from "./CellOutput";
import {
  type CellRunState,
  mergeIntoCell,
  reduceCellEvent,
  startRun,
} from "./cellEvents";
import {
  type NbCell,
  type Notebook,
  cellSource,
  emptyNotebook,
  parseNotebook,
} from "./types";

export function NotebookRenderer({ path }: { path: string }) {
  const { entry, setText, save } = useFileBuffer(path);

  if (entry.status === "loading") return <Status>Loading {path}…</Status>;
  if (entry.status === "error") {
    return <Status tone="err">{entry.error ?? "load failed"}</Status>;
  }
  if (entry.kind !== "text") {
    return <Status>Binary file — cannot parse as notebook.</Status>;
  }

  let nb: Notebook;
  try {
    nb = entry.text.trim() === "" ? emptyNotebook() : parseNotebook(entry.text);
  } catch (err) {
    return (
      <Status tone="err">
        Failed to parse notebook: {err instanceof Error ? err.message : String(err)}
      </Status>
    );
  }

  return (
    <NotebookBody
      path={path}
      initial={nb}
      bufferText={entry.text}
      onPersist={setText}
      onSave={save}
    />
  );
}

function NotebookBody({
  path,
  initial,
  bufferText,
  onPersist,
  onSave,
}: {
  path: string;
  initial: Notebook;
  bufferText: string;
  onPersist: (text: string) => void;
  onSave: () => Promise<void>;
}) {
  // The kernel lives on the investigation; null when rendered elsewhere.
  const investigationId = useOptionalAgent()?.investigationId ?? null;
  const slug = useWorkspaceSlug();
  const [nb, setNb] = useState<Notebook>(initial);
  // run state keyed by cell index; lives outside `cells` so re-renders
  // don't churn the underlying NbCell shape until cell_done persists.
  const [runs, setRuns] = useState<Map<number, CellRunState>>(new Map());
  const abortRefs = useRef<Map<number, AbortController>>(new Map());
  const nbRef = useRef<Notebook>(initial);
  // Tracks the JSON we last wrote so an external buffer change (the agent,
  // or the other split pane) re-seeds our local state, but our own writes
  // don't bounce back and reset the cursor.
  const lastSerialized = useRef<string>(bufferText);

  useEffect(() => {
    if (bufferText === lastSerialized.current) return;
    try {
      const fresh = bufferText.trim() === "" ? emptyNotebook() : parseNotebook(bufferText);
      lastSerialized.current = bufferText;
      setNb(fresh);
      nbRef.current = fresh;
    } catch {
      // ignore transient parse errors mid-edit on the other side
    }
  }, [bufferText]);

  useEffect(() => {
    nbRef.current = nb;
  }, [nb]);

  const updateCellSource = (i: number, src: string) => {
    setNb((prev) => {
      const next = [...prev.cells];
      const c = next[i];
      if (!c) return prev;
      next[i] = { ...c, source: src };
      const updated = { ...prev, cells: next };
      void persist(updated);
      return updated;
    });
  };

  const persist = useCallback(
    async (notebook: Notebook) => {
      const json = JSON.stringify(notebook, null, 2);
      lastSerialized.current = json;
      onPersist(json); // routes through the shared buffer (autosave + sync)
    },
    [onPersist],
  );

  const runCell = useCallback(
    async (idx: number) => {
      // No kernel outside an investigation (e.g. a notebook opened in a KB
      // collection) — render-only, cells don't run.
      if (!investigationId) return;
      const cell = nbRef.current.cells[idx];
      if (!cell || cell.cell_type !== "code") return;
      const code = cellSource(cell);

      let local: CellRunState = startRun();
      setRuns((prev) => {
        const next = new Map(prev);
        next.set(idx, local);
        return next;
      });

      const controller = new AbortController();
      abortRefs.current.set(idx, controller);

      try {
        for await (const ev of api.streamCellEvents({
          slug,
          investigationId,
          notebookPath: path,
          cellIndex: idx,
          code,
          signal: controller.signal,
        })) {
          local = reduceCellEvent(local, ev);
          const snapshot = local;
          setRuns((prev) => {
            const next = new Map(prev);
            next.set(idx, snapshot);
            return next;
          });
          if (ev.type === "cell_done") {
            setNb((prev) => {
              const cells = [...prev.cells];
              const before = cells[idx];
              if (!before) return prev;
              cells[idx] = mergeIntoCell(before, snapshot);
              const next = { ...prev, cells };
              void persist(next);
              return next;
            });
          }
        }
      } catch (err: unknown) {
        if ((err as { name?: string } | null)?.name === "AbortError") return;
        console.error("cell run failed", err);
      } finally {
        abortRefs.current.delete(idx);
        // Running persists the notebook (source + outputs) — explicit-save
        // model means a run is the notebook's "save". No-op when clean.
        void onSave();
      }
    },
    [investigationId, path, persist, onSave],
  );

  const interruptCell = (idx: number) => {
    abortRefs.current.get(idx)?.abort();
    if (!investigationId) return;
    // BE-side stop signal: the kernel keeps running the cell to
    // completion otherwise, even after we've stopped listening.
    void api.interruptCell({
      slug,
      investigationId,
      notebookPath: path,
      cellIndex: idx,
    });
  };

  const runAllCells = useCallback(async () => {
    for (let i = 0; i < nbRef.current.cells.length; i++) {
      const c = nbRef.current.cells[i];
      if (c?.cell_type === "code") {
        await runCell(i);
      }
    }
  }, [runCell]);

  // Subscribe to tab-strip "Run all" events for this notebook.
  useEffect(() => onRunAll(path, () => void runAllCells()), [path, runAllCells]);

  const addCell = () => {
    setNb((prev) => ({
      ...prev,
      cells: [...prev.cells, { cell_type: "code", source: "", outputs: [], execution_count: null }],
    }));
  };

  if (nb.cells.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <p style={{ color: "var(--text-paper-d)" }}>
          Empty notebook. Add the first cell to get started.
        </p>
        <button
          type="button"
          onClick={addCell}
          style={{
            alignSelf: "flex-start",
            padding: "6px 12px",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            fontSize: 12,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <Icon name="plus" size={12} /> Add cell
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {nb.cells.map((cell, i) => (
        <Cell
          key={i}
          index={i}
          cell={cell}
          run={runs.get(i)}
          onChange={(src) => updateCellSource(i, src)}
          onRun={() => void runCell(i)}
          onInterrupt={() => interruptCell(i)}
        />
      ))}
      <button
        type="button"
        onClick={addCell}
        style={{
          alignSelf: "flex-start",
          padding: "6px 12px",
          border: "1px dashed var(--paper-3)",
          borderRadius: "var(--radius-btn)",
          fontSize: 12,
          color: "var(--text-paper-d)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <Icon name="plus" size={12} /> Add cell
      </button>
    </div>
  );
}

function Cell({
  index,
  cell,
  run,
  onChange,
  onRun,
  onInterrupt,
}: {
  index: number;
  cell: NbCell;
  run: CellRunState | undefined;
  onChange: (src: string) => void;
  onRun: () => void;
  onInterrupt: () => void;
}) {
  if (cell.cell_type === "markdown") {
    return (
      <div style={{ display: "flex", gap: 12 }}>
        <Gutter index={index} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <CardHeader kind="markdown" />
          <div className="md-body" style={{ marginTop: 4 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
              {cellSource(cell)}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    );
  }
  if (cell.cell_type === "raw") {
    return (
      <div style={{ display: "flex", gap: 12 }}>
        <Gutter index={index} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <CardHeader kind="raw" />
          <pre style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: 12 }}>
            {cellSource(cell)}
          </pre>
        </div>
      </div>
    );
  }

  // code cell
  const status = run?.status ?? "idle";
  const exec =
    run?.execution_count ?? cell.execution_count ?? (status === "running" ? "*" : " ");

  return (
    <div style={{ display: "flex", gap: 12 }}>
      <Gutter
        index={index}
        exec={String(exec)}
        running={status === "running"}
        onRun={onRun}
        onInterrupt={onInterrupt}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <CardHeader kind="python" status={status} durationMs={run?.durationMs ?? null} />
        <CellEditor value={cellSource(cell)} onChange={onChange} />
        {run && <CellOutput outputs={run.outputs} />}
        {!run && cell.outputs && cell.outputs.length > 0 && (
          <CellOutput outputs={cell.outputs} />
        )}
      </div>
    </div>
  );
}

function Gutter({
  index,
  exec,
  running,
  onRun,
  onInterrupt,
}: {
  index: number;
  exec?: string;
  running?: boolean;
  onRun?: () => void;
  onInterrupt?: () => void;
}) {
  return (
    <div
      style={{
        width: 32,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 4,
        paddingTop: 6,
      }}
    >
      {onRun ? (
        <button
          type="button"
          onClick={running ? onInterrupt : onRun}
          aria-label={running ? `interrupt cell ${index}` : `run cell ${index}`}
          style={{
            width: 28,
            height: 28,
            borderRadius: "50%",
            border: running ? "2px solid var(--accent)" : "1px solid var(--paper-3)",
            background: running ? "var(--accent-soft)" : "var(--white)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            color: running ? "var(--accent)" : "var(--text-paper)",
          }}
        >
          {running ? (
            <span style={{ width: 8, height: 8, background: "var(--accent)", borderRadius: 2 }} />
          ) : (
            <Icon name="play" size={12} />
          )}
        </button>
      ) : (
        <div style={{ width: 28, height: 28 }} />
      )}
      {exec !== undefined && (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-paper-d2)",
          }}
        >
          [{exec}]
        </div>
      )}
    </div>
  );
}

function CardHeader({
  kind,
  status,
  durationMs,
}: {
  kind: "python" | "markdown" | "raw";
  status?: "running" | "ok" | "error" | "idle";
  durationMs?: number | null;
}) {
  const pillTone =
    status === "running"
      ? "var(--accent)"
      : status === "ok"
        ? "var(--ok)"
        : status === "error"
          ? "var(--err)"
          : "var(--text-paper-d2)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--text-paper-d)",
        marginBottom: 4,
      }}
    >
      <span
        style={{
          padding: "1px 6px",
          background: "var(--paper-2)",
          borderRadius: 3,
          color: "var(--text-paper)",
        }}
      >
        {kind}
      </span>
      {status && status !== "idle" && (
        <span style={{ color: pillTone }}>
          ● {status === "running" ? "running…" : status === "ok"
            ? durationMs != null
              ? `ran in ${(durationMs / 1000).toFixed(2)}s`
              : "ran"
            : "error"}
        </span>
      )}
    </div>
  );
}

function Status({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "err";
}) {
  return (
    <div
      style={{
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
        fontSize: "var(--text-body)",
      }}
    >
      {children}
    </div>
  );
}
