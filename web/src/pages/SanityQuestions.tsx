/**
 * 題目管理 panel (#231 P8) — author custom sanity questions (no code). The
 * built-in questions carry Python graders and are read-only; custom ones have no
 * mechanical grader (AI-only judged), so the form only collects prompt / 參考答案
 * / 題組 / which efforts to run. Wire shapes: `api/sanity.ts` (typed CRUD over
 * the CustomSanityQuestion resource).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type CustomQuestion,
  type CustomQuestionBody,
  type SanityApi,
  sanityApi,
} from "../api/sanity";
import { qk } from "../api/queryKeys";
import { pxToRem } from "../lib/pxToRem";

const LEVELS = ["none", "low", "medium", "high"];
const EMPTY: CustomQuestionBody = {
  category: "",
  prompt: "",
  expected: "",
  levels: ["none"],
  enabled: true,
};

const field = {
  width: "100%",
  padding: "6px 8px",
  borderRadius: 6,
  border: "1px solid var(--paper-3)",
  background: "var(--paper-2)",
  fontSize: "var(--text-body-sm)",
};

export function SanityQuestions({ client = sanityApi }: { client?: SanityApi }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<CustomQuestionBody>(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);

  const { data: custom } = useQuery({ queryKey: qk.sanity.custom, queryFn: () => client.listCustom() });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: qk.sanity.custom });
    queryClient.invalidateQueries({ queryKey: qk.sanity.meta });
  };
  const reset = () => {
    setForm(EMPTY);
    setEditing(null);
  };

  const save = useMutation({
    mutationFn: () =>
      editing ? client.updateCustom(editing, form) : client.createCustom(form),
    onSuccess: () => {
      invalidate();
      reset();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => client.deleteCustom(id),
    onSuccess: invalidate,
  });

  const startEdit = (q: CustomQuestion) => {
    setEditing(q.id);
    setForm({
      category: q.category,
      prompt: q.prompt,
      expected: q.expected,
      levels: q.levels,
      enabled: q.enabled,
    });
  };

  const toggleLevel = (lvl: string) =>
    setForm((f) => ({
      ...f,
      levels: f.levels.includes(lvl) ? f.levels.filter((l) => l !== lvl) : [...f.levels, lvl],
    }));

  const canSave = form.prompt.trim() !== "" && form.expected.trim() !== "" && form.category.trim() !== "";

  return (
    <div data-testid="sanity-questions" style={{ marginTop: 20 }}>
      <strong style={{ fontSize: pxToRem(13) }}>題目管理</strong>
      <p style={{ margin: "4px 0 10px", fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
        自訂題目由 AI 評分(無機械評分)。內建題目不可編輯。
      </p>

      {/* author / edit form */}
      <div style={{ display: "grid", gap: 8, maxWidth: 560 }}>
        <input
          data-testid="q-category"
          placeholder="題組(例如:格式輸出)"
          value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })}
          style={field}
        />
        <textarea
          data-testid="q-prompt"
          placeholder="題目(給模型的提問)"
          value={form.prompt}
          onChange={(e) => setForm({ ...form, prompt: e.target.value })}
          rows={2}
          style={field}
        />
        <textarea
          data-testid="q-expected"
          placeholder="參考答案 / 期望行為(餵給 AI 評審)"
          value={form.expected}
          onChange={(e) => setForm({ ...form, expected: e.target.value })}
          rows={2}
          style={field}
        />
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>要跑的深度</span>
          {LEVELS.map((lvl) => (
            <label key={lvl} style={{ display: "inline-flex", gap: 4, fontSize: "var(--text-body-sm)" }}>
              <input
                type="checkbox"
                data-testid={`q-level-${lvl}`}
                checked={form.levels.includes(lvl)}
                onChange={() => toggleLevel(lvl)}
              />
              {lvl}
            </label>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="q-save"
            disabled={!canSave || save.isPending}
            onClick={() => save.mutate()}
            style={{
              padding: "6px 14px",
              borderRadius: 7,
              border: "1px solid var(--paper-3)",
              background: "var(--accent-soft)",
              color: "var(--accent-h)",
              fontSize: "var(--text-body-sm)",
              fontWeight: 600,
              cursor: !canSave || save.isPending ? "default" : "pointer",
              opacity: !canSave || save.isPending ? 0.6 : 1,
            }}
          >
            {editing ? "更新題目" : "新增題目"}
          </button>
          {editing && (
            <button
              type="button"
              data-testid="q-cancel"
              onClick={reset}
              style={{
                padding: "6px 14px",
                borderRadius: 7,
                border: "1px solid var(--paper-3)",
                background: "var(--paper-2)",
                fontSize: "var(--text-body-sm)",
                cursor: "pointer",
              }}
            >
              取消
            </button>
          )}
        </div>
      </div>

      {/* existing custom questions */}
      <ul style={{ listStyle: "none", margin: "14px 0 0", padding: 0 }}>
        {(custom ?? []).map((q) => (
          <li
            key={q.id}
            data-testid={`custom-row-${q.id}`}
            style={{
              display: "flex",
              gap: 10,
              alignItems: "flex-start",
              padding: "8px 0",
              borderTop: "1px solid var(--paper-3)",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: "var(--text-body-sm)", fontWeight: 500 }}>
                [{q.category}] {q.prompt}
                {!q.enabled && <span style={{ color: "var(--text-paper-d)" }}> · 已停用</span>}
              </div>
              <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                ↳ {q.expected} · {q.levels.join(", ") || "none"}
              </div>
            </div>
            <button
              type="button"
              data-testid={`q-edit-${q.id}`}
              onClick={() => startEdit(q)}
              style={{ border: "none", background: "none", color: "var(--accent-h)", cursor: "pointer" }}
            >
              編輯
            </button>
            <button
              type="button"
              data-testid={`q-delete-${q.id}`}
              onClick={() => remove.mutate(q.id)}
              style={{ border: "none", background: "none", color: "var(--warn)", cursor: "pointer" }}
            >
              刪除
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
