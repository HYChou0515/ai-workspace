/**
 * The card-gen review surface (#175, extracted for #415). Given a run's drafted
 * proposals, it offers two views of the same set — a structured per-card list
 * (accept / reject / edit, with the source provenance as the audit "依據", and an
 * `update` proposal shown against the card it would overwrite) and one editable
 * todo.md document for bulk body edits — plus save-progress / commit actions.
 *
 * It owns no data fetching: the modal (#175) and the persistent 待審核 tab (#415
 * P4) both drive it with proposals + callbacks, so the same review UI serves a
 * live modal job and a durable CardGenRun.
 */
import { useState } from "react";

import type { KbCardGenCommit, KbContextCard, KbProposedCard } from "../../api/kb";
import { parseTodo, serializeTodo } from "./cardGenTodo";

export function CardGenReview({
  proposals,
  existingCards,
  onChange,
  onSave,
  onCommit,
  committed,
  saving = false,
  committing = false,
}: {
  proposals: KbProposedCard[];
  existingCards: KbContextCard[];
  onChange: (next: KbProposedCard[]) => void;
  onSave: () => void;
  onCommit: () => void;
  committed: KbCardGenCommit | null;
  saving?: boolean;
  committing?: boolean;
}) {
  const [view, setView] = useState<"list" | "todo">("list");

  const setProposal = (i: number, patch: Partial<KbProposedCard>) =>
    onChange(proposals.map((p, j) => (j === i ? { ...p, ...patch } : p)));

  const acceptedCount = proposals.filter((p) => p.decision === "accepted").length;

  if (proposals.length === 0) {
    return <p data-testid="cardgen-empty">沒有新卡片可建議。</p>;
  }

  return (
    <>
      <div className="kb-cardgen__viewtoggle">
        <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>
          逐卡清單
        </button>
        <button type="button" aria-pressed={view === "todo"} onClick={() => setView("todo")}>
          todo.md
        </button>
      </div>

      {view === "list" ? (
        <ul className="kb-cardgen__proposals">
          {proposals.map((p, i) => {
            const target = existingCards.find((c) => c.id === p.target_card_id);
            return (
              <li
                key={i}
                className={`kb-cardgen__proposal is-${p.decision}`}
                data-testid="cardgen-proposal"
              >
                <div className="kb-cardgen__badges">
                  <span className={`kb-cardgen__badge is-${p.mode}`}>{p.mode}</span>
                  {!p.confident && (
                    <span className="kb-cardgen__badge is-uncertain">⚠️ 不確定</span>
                  )}
                </div>
                <input
                  aria-label={`Title ${i}`}
                  value={p.title}
                  onChange={(e) => setProposal(i, { title: e.target.value })}
                />
                <div className="kb-cardgen__keys">{p.keys.join(", ")}</div>
                {p.mode === "update" && target && (
                  <details className="kb-cardgen__diff">
                    <summary>目前卡片內容（將被覆蓋）</summary>
                    <pre>{target.body}</pre>
                  </details>
                )}
                <textarea
                  aria-label={`Body ${i}`}
                  value={p.body}
                  onChange={(e) => setProposal(i, { body: e.target.value })}
                />
                <ul className="kb-cardgen__prov">
                  {p.provenance.map((pr, k) => (
                    <li key={k}>
                      <span className="kb-cardgen__prov-path">{pr.path}</span>
                      <span className="kb-cardgen__prov-snip">{pr.snippet}</span>
                    </li>
                  ))}
                </ul>
                <div className="kb-cardgen__decide">
                  <button
                    type="button"
                    aria-pressed={p.decision === "accepted"}
                    onClick={() => setProposal(i, { decision: "accepted" })}
                  >
                    接受
                  </button>
                  <button
                    type="button"
                    aria-pressed={p.decision === "rejected"}
                    onClick={() => setProposal(i, { decision: "rejected" })}
                  >
                    拒絕
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <textarea
          aria-label="todo.md"
          className="kb-cardgen__todo"
          value={serializeTodo(proposals)}
          onChange={(e) => onChange(parseTodo(e.target.value, proposals))}
        />
      )}

      <footer className="kb-cardgen__foot">
        {committed ? (
          <span data-testid="cardgen-committed">
            已建立 {committed.created} · 更新 {committed.updated} · 略過 {committed.skipped}
          </span>
        ) : (
          <>
            <button type="button" onClick={onSave} disabled={saving}>
              儲存進度
            </button>
            <button
              type="button"
              disabled={acceptedCount === 0 || committing}
              onClick={onCommit}
            >
              套用已接受（{acceptedCount}）
            </button>
          </>
        )}
      </footer>
    </>
  );
}
