/**
 * #613 P2: the pinned todo checklist next to the chat.
 *
 * The agent maintains the list via its `update_todos` tool (whole-list replace,
 * streamed live as `todos_updated` into the query cache); the user edits it here
 * when no turn is running. Editing locks while a turn streams (`streaming`) so
 * the two writers never interleave — the agent's next write is the truth.
 * Collapsible like WorkflowProgress; hidden entirely when there is nothing to
 * show and nothing the viewer could add.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { itemTodosApi, type ItemTodosApi, type TodoItem } from "../api/itemTodos";
import { qk } from "../api/queryKeys";
import { usePersistentBoolean } from "../hooks/usePersistentBoolean";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

export function TodoPanel({
  slug,
  itemId,
  chatId,
  streaming,
  readOnly,
  client = itemTodosApi,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  /** A turn is streaming — the agent owns the list right now; editing locks. */
  streaming: boolean;
  /** Viewer can't converse on this item — no edit affordances at all. */
  readOnly: boolean;
  client?: ItemTodosApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [expanded, setExpanded] = usePersistentBoolean("chat.todos.expanded", true);
  const [draft, setDraft] = useState("");
  const key = qk.itemChatTodos(slug, itemId, chatId);
  const { data: items = [] } = useQuery({
    queryKey: key,
    queryFn: () => client.getTodos(slug, itemId, chatId),
  });
  const put = useMutation({
    mutationFn: (next: TodoItem[]) => client.putTodos(slug, itemId, chatId, next),
    // PUT returns the saved list — it IS the cache truth (no refetch round-trip).
    onSuccess: (saved) => qc.setQueryData(key, saved),
  });
  const locked = streaming || readOnly || put.isPending;

  // Nothing to show and nothing the viewer could add — stay out of the way.
  if (items.length === 0 && (readOnly || streaming)) return null;

  const done = items.filter((i) => i.status === "completed").length;
  const toggle = (idx: number) =>
    put.mutate(
      items.map((it, i) =>
        i === idx
          ? { ...it, status: it.status === "completed" ? "pending" : "completed" }
          : it,
      ),
    );
  const remove = (idx: number) => put.mutate(items.filter((_, i) => i !== idx));
  const add = () => {
    const text = draft.trim();
    if (!text) return;
    put.mutate([...items, { text, status: "pending" }]);
    setDraft("");
  };

  return (
    <section
      data-testid="todo-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "8px 12px",
        borderBottom: "1px solid var(--paper-3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: pxToRem(12), fontWeight: 600 }}>{t("todos.title")}</span>
        <span
          data-testid="todo-count"
          style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}
        >
          {done}/{items.length}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="todo-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? t("todos.collapse") : t("todos.expand")}
        </button>
      </div>

      {expanded && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
          {items.map((it, idx) => (
            <li
              key={`${idx}-${it.text}`}
              style={{ display: "flex", alignItems: "center", gap: 8, fontSize: pxToRem(12) }}
            >
              <input
                type="checkbox"
                checked={it.status === "completed"}
                disabled={locked}
                onChange={() => toggle(idx)}
              />
              <span
                style={{
                  flex: 1,
                  color: it.status === "completed" ? "var(--text-paper-d)" : undefined,
                  textDecoration: it.status === "completed" ? "line-through" : undefined,
                }}
              >
                {it.text}
              </span>
              {it.status === "in_progress" && (
                <span
                  data-testid="todo-in-progress"
                  style={{ fontSize: pxToRem(10), color: "var(--accent)" }}
                >
                  {t("todos.inProgress")}
                </span>
              )}
              {!readOnly && (
                <button
                  type="button"
                  className="btn"
                  data-variant="secondary"
                  data-size="sm"
                  data-testid="todo-remove"
                  aria-label={t("todos.remove")}
                  disabled={locked}
                  onClick={() => remove(idx)}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {expanded && !readOnly && (
        <div style={{ display: "flex", gap: 6 }}>
          <input
            data-testid="todo-add-input"
            value={draft}
            disabled={locked}
            placeholder={t("todos.add.placeholder")}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") add();
            }}
            style={{ flex: 1, fontSize: pxToRem(12) }}
          />
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="todo-add"
            disabled={locked || !draft.trim()}
            onClick={add}
          >
            {t("todos.add")}
          </button>
        </div>
      )}
    </section>
  );
}
