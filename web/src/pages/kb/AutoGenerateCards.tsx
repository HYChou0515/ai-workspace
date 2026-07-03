/**
 * #175 自動 context card — the in-tab "auto-generate" flow.
 *
 * A modal with three steps: pick documents (by updated time) → a background
 * generation job drafts cards (polled) → review the proposals and commit the
 * accepted ones. The heavy work runs on the backend `CardGenJob`; this component
 * is the trigger + the human review surface (grill #175).
 *
 * Review offers two views of the same proposals: a structured per-card list
 * (accept / reject / edit, with the source provenance as the audit "依據", and an
 * `update` proposal shown against the card it would overwrite) and a single
 * editable todo.md document for bulk body edits. Decisions + edits persist back
 * onto the job (resumable) before commit.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  kbApi,
  type KbApi,
  type KbCardGenCommit,
  type KbProposedCard,
} from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { parseTodo, serializeTodo } from "./cardGenTodo";
import { fetchAllDocs } from "./useCollectionDocs";

type Step = "select" | "generating" | "review";

export function AutoGenerateCards({
  collectionId,
  client = kbApi,
  onClose,
  onCommitted,
}: {
  collectionId: string;
  client?: KbApi;
  onClose: () => void;
  onCommitted?: (result: KbCardGenCommit) => void;
}) {
  const [step, setStep] = useState<Step>("select");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [proposals, setProposals] = useState<KbProposedCard[]>([]);
  const [view, setView] = useState<"list" | "todo">("list");
  const [committed, setCommitted] = useState<KbCardGenCommit | null>(null);

  // ── step 1: pick documents, newest first ───────────────────────────────
  // Share the SAME key + fetcher as the collection page's index-status strip
  // (fetchAllDocs, #162): it returns a bare KbDocument[] and pages within the
  // BE's limit≤500. Using our own {items} fetch under this shared key would (a)
  // read the strip's bare array as `undefined` items and (b) 422 on limit=1000
  // — the two bugs behind #394's empty picker.
  const { data: docList } = useQuery({
    queryKey: qk.kb.documents(collectionId),
    queryFn: () => fetchAllDocs(client, collectionId),
    enabled: step === "select",
  });
  const docs = useMemo(() => {
    const items = [...(docList ?? [])];
    items.sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
    const term = search.trim().toLowerCase();
    return term ? items.filter((d) => d.path.toLowerCase().includes(term)) : items;
  }, [docList, search]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const generateMut = useMutation({
    mutationFn: () => client.generateContextCards(collectionId, [...selected]),
    onSuccess: (id) => {
      setJobId(id);
      setStep("generating");
    },
  });

  // ── step 2: poll the run until it completes ─────────────────────────────
  const genStatus = useQuery({
    queryKey: qk.kb.cardGen(jobId ?? ""),
    enabled: step === "generating" && jobId !== null,
    queryFn: () => client.getCardGenStatus(jobId as string),
    refetchInterval: (q) => {
      const st = q.state.data?.status;
      return st === "pending" || st === "processing" ? 1000 : false;
    },
  });
  useEffect(() => {
    if (step === "generating" && genStatus.data?.status === "completed") {
      setProposals(genStatus.data.proposals);
      setStep("review");
    }
  }, [step, genStatus.data]);
  const failed = genStatus.data?.status === "failed";

  // existing cards — to show an `update` proposal against what it overwrites.
  const { data: existingCards = [] } = useQuery({
    queryKey: qk.kb.contextCards(collectionId),
    queryFn: () => client.listContextCards(collectionId),
    enabled: step === "review",
  });

  const setProposal = (i: number, patch: Partial<KbProposedCard>) =>
    setProposals((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)));

  const reviewMut = useMutation({
    mutationFn: () => client.reviewCardGen(jobId as string, proposals),
  });
  const commitMut = useMutation({
    mutationFn: async () => {
      await client.reviewCardGen(jobId as string, proposals); // persist before committing
      return client.commitCardGen(jobId as string);
    },
    onSuccess: (r) => {
      setCommitted(r);
      onCommitted?.(r);
    },
  });

  const acceptedCount = proposals.filter((p) => p.decision === "accepted").length;

  // ── render ──────────────────────────────────────────────────────────────
  return (
    <div className="kb-cardgen__backdrop" role="dialog" aria-label="Auto-generate context cards">
      <div className="kb-cardgen__modal">
        <header className="kb-cardgen__head">
          <strong>自動 context card</strong>
          <button type="button" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        {step === "select" && (
          <div className="kb-cardgen__body">
            <p>挑選要產生卡片的文件（依更新時間排序）。系統會讀取文件、草擬卡片讓你審核。</p>
            <input
              aria-label="Search documents"
              placeholder="Filter documents…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <ul className="kb-cardgen__docs">
              {docs.length === 0 ? (
                <li className="kb-cardgen__none">No documents.</li>
              ) : (
                docs.map((d) => (
                  <li key={d.resource_id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={selected.has(d.resource_id)}
                        onChange={() => toggle(d.resource_id)}
                      />
                      <span>{d.path}</span>
                    </label>
                  </li>
                ))
              )}
            </ul>
            <footer className="kb-cardgen__foot">
              <button type="button" onClick={onClose}>
                取消
              </button>
              <button
                type="button"
                disabled={selected.size === 0 || generateMut.isPending}
                onClick={() => generateMut.mutate()}
              >
                自動生成（{selected.size}）
              </button>
            </footer>
          </div>
        )}

        {step === "generating" && (
          <div className="kb-cardgen__body" data-testid="cardgen-generating">
            {failed ? (
              <p data-testid="cardgen-failed">生成失敗，請重試。</p>
            ) : (
              <p aria-busy="true">正在從文件草擬卡片…</p>
            )}
          </div>
        )}

        {step === "review" && (
          <div className="kb-cardgen__body">
            {proposals.length === 0 ? (
              <p data-testid="cardgen-empty">沒有新卡片可建議。</p>
            ) : (
              <>
                <div className="kb-cardgen__viewtoggle">
                  <button
                    type="button"
                    aria-pressed={view === "list"}
                    onClick={() => setView("list")}
                  >
                    逐卡清單
                  </button>
                  <button
                    type="button"
                    aria-pressed={view === "todo"}
                    onClick={() => setView("todo")}
                  >
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
                    onChange={(e) => setProposals(parseTodo(e.target.value, proposals))}
                  />
                )}

                <footer className="kb-cardgen__foot">
                  {committed ? (
                    <span data-testid="cardgen-committed">
                      已建立 {committed.created} · 更新 {committed.updated} · 略過{" "}
                      {committed.skipped}
                    </span>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => reviewMut.mutate()}
                        disabled={reviewMut.isPending}
                      >
                        儲存進度
                      </button>
                      <button
                        type="button"
                        disabled={acceptedCount === 0 || commitMut.isPending}
                        onClick={() => commitMut.mutate()}
                      >
                        套用已接受（{acceptedCount}）
                      </button>
                    </>
                  )}
                  <button type="button" onClick={onClose}>
                    {committed ? "完成" : "關閉"}
                  </button>
                </footer>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
