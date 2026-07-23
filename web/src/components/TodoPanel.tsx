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

import { itemGoalApi, type GoalRead, type ItemGoalApi } from "../api/itemGoal";
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
  goalClient = itemGoalApi,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  /** A turn is streaming — the agent owns the list right now; editing locks. */
  streaming: boolean;
  /** Viewer can't converse on this item — no edit affordances at all. */
  readOnly: boolean;
  client?: ItemTodosApi;
  goalClient?: ItemGoalApi;
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
  // #613 P3: the chat's goal — condition + state + rounds above the checklist.
  const goalKey = qk.itemChatGoal(slug, itemId, chatId);
  const { data: goalRead } = useQuery<GoalRead>({
    queryKey: goalKey,
    queryFn: () => goalClient.getGoal(slug, itemId, chatId),
  });
  const [goalDraft, setGoalDraft] = useState("");
  const setGoal = useMutation({
    mutationFn: (condition: string) => goalClient.putGoal(slug, itemId, chatId, condition),
    onSuccess: (saved) => qc.setQueryData(goalKey, saved),
  });
  const clearGoal = useMutation({
    mutationFn: () => goalClient.deleteGoal(slug, itemId, chatId),
    onSuccess: () =>
      qc.setQueryData(goalKey, (old: GoalRead | undefined) => ({
        goal: null,
        checker_enabled: old?.checker_enabled ?? true,
      })),
  });
  const goal = goalRead?.goal ?? null;
  const locked = streaming || readOnly || put.isPending;
  const goalLocked = streaming || readOnly || setGoal.isPending || clearGoal.isPending;

  // Nothing to show and nothing the viewer could add — stay out of the way.
  if (items.length === 0 && goal === null && (readOnly || streaming)) return null;

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

      {expanded && goal !== null && (
        <div
          data-testid="goal-row"
          style={{ display: "flex", alignItems: "center", gap: 8, fontSize: pxToRem(12) }}
        >
          <span style={{ fontWeight: 600 }}>{t("goal.title")}</span>
          <span style={{ flex: 1 }}>{goal.condition}</span>
          {goal.state === "active" && (
            <span data-testid="goal-rounds" style={{ color: "var(--text-paper-d)" }}>
              {t("goal.round", { k: goal.rounds_used, n: goal.max_rounds })}
            </span>
          )}
          {goal.state === "met" && (
            <span data-testid="goal-met" style={{ color: "var(--ok, green)" }}>
              {t("goal.met")}
            </span>
          )}
          {goal.state === "exhausted" && (
            <span data-testid="goal-exhausted" style={{ color: "var(--err)" }}>
              {t("goal.exhausted")}
            </span>
          )}
          {!readOnly && (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="goal-clear"
              disabled={goalLocked}
              onClick={() => clearGoal.mutate()}
            >
              {t("goal.clear")}
            </button>
          )}
        </div>
      )}
      {expanded && goal !== null && goalRead?.checker_enabled === false && (
        <div
          data-testid="goal-no-checker"
          style={{ fontSize: pxToRem(11), color: "var(--err)" }}
        >
          {t("goal.noChecker")}
        </div>
      )}
      {expanded && goal === null && !readOnly && (
        <div style={{ display: "flex", gap: 6 }}>
          <input
            data-testid="goal-input"
            value={goalDraft}
            disabled={goalLocked}
            placeholder={t("goal.placeholder")}
            onChange={(e) => setGoalDraft(e.target.value)}
            style={{ flex: 1, fontSize: pxToRem(12) }}
          />
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="goal-set"
            disabled={goalLocked || !goalDraft.trim()}
            onClick={() => {
              const v = goalDraft.trim();
              if (!v) return;
              setGoal.mutate(v);
              setGoalDraft("");
            }}
          >
            {t("goal.set")}
          </button>
        </div>
      )}

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
