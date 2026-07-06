/**
 * ReviewTable (#481) — the one filterable table behind the global 審核 inbox and,
 * scoped, each collection's 待審核 tab. One row per atomic item: a card-gen
 * proposal (the `run` it belongs to is a column, groupable) or a clarification
 * question. Inline accept/reject on card rows, a right drawer for detail + editing
 * + answering, and a bulk "套用選取" over selected cards. Read-only rows (a
 * collection the user can see but not write) show their actions disabled.
 */
import { useMemo, useState } from "react";

import type { KbCardRef, KbReviewCard, KbReviewQuestion } from "../../api/kb";
import { Btn } from "../../components/Btn";
import { Icon } from "../../components/Icon";
import type { useReviewInbox } from "../../hooks/useReviewInbox";
import { type MsgKey, useT } from "../../lib/i18n";
import { ReviewDrawer } from "./ReviewDrawer";

type Actions = ReturnType<typeof useReviewInbox>;

type CardRow = { kind: "card"; key: string; data: KbReviewCard };
type QRow = { kind: "question"; key: string; data: KbReviewQuestion };
type Row = CardRow | QRow;

const cardKey = (c: KbReviewCard) => `${c.run_id}:${c.card.id}`;
const runTag = (runId: string) => `#${runId.slice(0, 4)}`;

function toRows(cards: KbReviewCard[], questions: KbReviewQuestion[]): Row[] {
  return [
    ...cards.map((data): Row => ({ kind: "card", key: cardKey(data), data })),
    ...questions.map((data): Row => ({ kind: "question", key: `q:${data.question.id}`, data })),
  ];
}

function matches(row: Row, needle: string): boolean {
  if (!needle) return true;
  const n = needle.toLowerCase();
  if (row.kind === "card") {
    const c = row.data.card;
    return (
      c.title.toLowerCase().includes(n) ||
      c.body.toLowerCase().includes(n) ||
      c.keys.some((k) => k.toLowerCase().includes(n))
    );
  }
  const q = row.data.question;
  return (
    q.term.toLowerCase().includes(n) ||
    q.question_text.toLowerCase().includes(n) ||
    q.quote.toLowerCase().includes(n)
  );
}

export function ReviewTable({
  cards,
  questions,
  resolved,
  actions,
  scoped = false,
}: {
  cards: KbReviewCard[];
  questions: KbReviewQuestion[];
  resolved: boolean;
  actions: Actions;
  scoped?: boolean;
}) {
  const t = useT();
  const [search, setSearch] = useState("");
  const [collection, setCollection] = useState("");
  const [type, setType] = useState<"all" | "card" | "question">("all");
  const [actionableOnly, setActionableOnly] = useState(false);
  const [groupByRun, setGroupByRun] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Row | null>(null);

  const allRows = useMemo(() => toRows(cards, questions), [cards, questions]);
  const collectionNames = useMemo(
    () => [...new Set(allRows.map((r) => r.data.collection_name))].sort(),
    [allRows],
  );

  const rows = allRows.filter((r) => {
    if (type !== "all" && r.kind !== type) return false;
    if (collection && r.data.collection_name !== collection) return false;
    if (actionableOnly && !r.data.can_act) return false;
    return matches(r, search);
  });

  const selectedRefs: KbCardRef[] = rows
    .filter((r): r is CardRow => r.kind === "card" && selected.has(r.key))
    .map((r) => ({ run_id: r.data.run_id, card_id: r.data.card.id }));

  const toggle = (key: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const applySelected = () => {
    if (selectedRefs.length === 0) return;
    actions.commit.mutate(selectedRefs);
    setSelected(new Set());
  };

  const decide = (row: CardRow, decision: "accepted" | "rejected") =>
    actions.decide.mutate({
      runId: row.data.run_id,
      cardId: row.data.card.id,
      decision: row.data.card.decision === decision ? "pending" : decision,
    });

  const nothingAtAll = allRows.length === 0;
  const colCount = scoped ? 5 : 6;

  return (
    <div className="rvw">
      <div className="rvw__toolbar" role="search">
        <label className="rvw__search">
          <Icon name="search" size={14} color="var(--text-paper-d2)" />
          <input
            type="search"
            aria-label={t("review.filter.search")}
            placeholder={t("review.filter.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        {!scoped && (
          <select
            className="inline-edit"
            aria-label={t("review.filter.collection")}
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
          >
            <option value="">{t("review.filter.collection")}</option>
            {collectionNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        )}
        <select
          className="inline-edit"
          aria-label={t("review.filter.type")}
          value={type}
          onChange={(e) => setType(e.target.value as typeof type)}
        >
          <option value="all">{t("review.filter.type")}</option>
          <option value="card">{t("review.type.card")}</option>
          <option value="question">{t("review.type.question")}</option>
        </select>
        <label className="rvw__check">
          <input
            type="checkbox"
            checked={actionableOnly}
            onChange={(e) => setActionableOnly(e.target.checked)}
          />
          {t("review.filter.actionable")}
        </label>
        <label className="rvw__check">
          <input
            type="checkbox"
            checked={groupByRun}
            onChange={(e) => setGroupByRun(e.target.checked)}
          />
          {t("review.groupByRun")}
        </label>
      </div>

      {nothingAtAll ? (
        <div className="rvw__empty">
          {resolved ? t("review.empty.resolved") : t("review.empty")}
        </div>
      ) : rows.length === 0 ? (
        <div className="rvw__empty">{t("review.empty.filtered")}</div>
      ) : (
        <div className="rvw__scroll">
          <table className="rvw__table">
            <thead>
              <tr>
                <th className="rvw__c-sel" aria-hidden />
                <th>{t("review.col.type")}</th>
                <th>{t("review.col.item")}</th>
                {!scoped && <th>{t("review.col.collection")}</th>}
                <th>{t("review.col.status")}</th>
                <th className="rvw__c-act">{t("review.col.actions")}</th>
              </tr>
            </thead>
            {groupByRun ? (
              <GroupedBody
                rows={rows}
                scoped={scoped}
                resolved={resolved}
                colCount={colCount}
                selected={selected}
                onToggle={toggle}
                onOpen={setOpen}
                onDecide={decide}
                onApplyRun={(refs) => actions.commit.mutate(refs)}
              />
            ) : (
              <tbody>
                {rows.map((row) => (
                  <RowView
                    key={row.key}
                    row={row}
                    scoped={scoped}
                    resolved={resolved}
                    selected={selected.has(row.key)}
                    onToggle={toggle}
                    onOpen={setOpen}
                    onDecide={decide}
                  />
                ))}
              </tbody>
            )}
          </table>
        </div>
      )}

      {selectedRefs.length > 0 && (
        <div className="rvw__bulk" role="region" aria-label={t("review.action.apply")}>
          <span>{t("review.selected", { n: selectedRefs.length })}</span>
          <Btn variant="primary" size="sm" onClick={applySelected}>
            {t("review.action.applySelected", { n: selectedRefs.length })}
          </Btn>
        </div>
      )}

      {open && (
        <ReviewDrawer
          item={open}
          resolved={resolved}
          actions={actions}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function Chip({ label, tone }: { label: string; tone: string }) {
  return (
    <span className="rvw__status" data-tone={tone}>
      {label}
    </span>
  );
}

function RowView({
  row,
  scoped,
  resolved,
  selected,
  onToggle,
  onOpen,
  onDecide,
}: {
  row: Row;
  scoped: boolean;
  resolved: boolean;
  selected: boolean;
  onToggle: (key: string) => void;
  onOpen: (row: Row) => void;
  onDecide: (row: CardRow, d: "accepted" | "rejected") => void;
}) {
  const t = useT();
  const selectable = row.kind === "card" && row.data.can_act && !resolved;
  return (
    <tr
      className="rvw__row"
      onClick={() => onOpen(row)}
      data-readonly={!row.data.can_act ? "" : undefined}
    >
      <td className="rvw__c-sel" onClick={(e) => e.stopPropagation()}>
        {selectable && (
          <input
            type="checkbox"
            aria-label={t("review.select")}
            checked={selected}
            onChange={() => onToggle(row.key)}
          />
        )}
      </td>
      <td>
        <span className="rvw__type" data-kind={row.kind}>
          {row.kind === "card" ? t("review.type.card") : t("review.type.question")}
        </span>
      </td>
      <td className="rvw__item">
        {row.kind === "card" ? <CardSummary row={row} /> : <QuestionSummary row={row} />}
      </td>
      {!scoped && <td className="rvw__coll">{row.data.collection_name}</td>}
      <td>
        {row.kind === "card" ? (
          <Chip
            label={t(`review.status.${row.data.card.decision}` as MsgKey)}
            tone={row.data.card.decision}
          />
        ) : (
          <Chip
            label={t(`review.status.${statusKey(row.data.question.status)}` as MsgKey)}
            tone={statusKey(row.data.question.status)}
          />
        )}
      </td>
      <td className="rvw__c-act" onClick={(e) => e.stopPropagation()}>
        <RowActions row={row} resolved={resolved} onOpen={onOpen} onDecide={onDecide} />
      </td>
    </tr>
  );
}

function statusKey(status: string): string {
  return status === "open" || status === "answered" || status === "discarded" ? status : "open";
}

function CardSummary({ row }: { row: CardRow }) {
  const t = useT();
  const c = row.data.card;
  return (
    <div className="rvw__summary">
      <div className="rvw__title-line">
        <span className="rvw__title">{c.title || c.keys[0] || "—"}</span>
        <span className="rvw__mode" data-mode={c.mode}>
          {c.mode === "update" ? t("review.mode.update") : t("review.mode.new")}
        </span>
        {!c.confident && <span className="rvw__warn">⚠️ {t("review.uncertain")}</span>}
        <span className="rvw__run" title={row.data.run_id}>
          {runTag(row.data.run_id)}
        </span>
      </div>
      <div className="rvw__keys">
        {c.keys.map((k) => (
          <code key={k} className="rvw__key">
            {k}
          </code>
        ))}
      </div>
    </div>
  );
}

function QuestionSummary({ row }: { row: QRow }) {
  const t = useT();
  const q = row.data.question;
  return (
    <div className="rvw__summary">
      <div className="rvw__title-line">
        <span className="rvw__kind" data-kind={q.kind}>
          {q.kind === "term" ? t("docq.kind.term") : t("docq.kind.description")}
        </span>
        {q.term && <span className="rvw__title">{q.term}</span>}
      </div>
      <div className="rvw__q">{q.question_text}</div>
    </div>
  );
}

function RowActions({
  row,
  resolved,
  onOpen,
  onDecide,
}: {
  row: Row;
  resolved: boolean;
  onOpen: (row: Row) => void;
  onDecide: (row: CardRow, d: "accepted" | "rejected") => void;
}) {
  const t = useT();
  if (!row.data.can_act) return <span className="rvw__readonly">{t("review.readonly")}</span>;
  // A resolved (history) row is view-only — click the row to open the drawer.
  if (resolved) {
    return (
      <button
        type="button"
        className="rvw__view"
        aria-label={t("review.col.item")}
        onClick={() => onOpen(row)}
      >
        <Icon name="eye" size={14} color="var(--text-paper-d)" />
      </button>
    );
  }
  if (row.kind === "question") {
    return (
      <Btn variant="secondary" size="sm" onClick={() => onOpen(row)}>
        {t("review.action.answer")}
      </Btn>
    );
  }
  const dec = row.data.card.decision;
  return (
    <div className="rvw__row-actions">
      <Btn
        variant={dec === "accepted" ? "primary" : "ghost"}
        size="sm"
        active={dec === "accepted"}
        onClick={() => onDecide(row, "accepted")}
      >
        {t("review.action.accept")}
      </Btn>
      <Btn
        variant={dec === "rejected" ? "danger" : "ghost"}
        size="sm"
        active={dec === "rejected"}
        onClick={() => onDecide(row, "rejected")}
      >
        {t("review.action.reject")}
      </Btn>
    </div>
  );
}

function GroupedBody({
  rows,
  scoped,
  resolved,
  colCount,
  selected,
  onToggle,
  onOpen,
  onDecide,
  onApplyRun,
}: {
  rows: Row[];
  scoped: boolean;
  resolved: boolean;
  colCount: number;
  selected: Set<string>;
  onToggle: (key: string) => void;
  onOpen: (row: Row) => void;
  onDecide: (row: CardRow, d: "accepted" | "rejected") => void;
  onApplyRun: (refs: KbCardRef[]) => void;
}) {
  const t = useT();
  const cardRows = rows.filter((r): r is CardRow => r.kind === "card");
  const qRows = rows.filter((r): r is QRow => r.kind === "question");
  const runIds = [...new Set(cardRows.map((r) => r.data.run_id))];

  return (
    <tbody>
      {runIds.map((runId) => {
        const group = cardRows.filter((r) => r.data.run_id === runId);
        const accepted = group.filter((r) => r.data.card.decision === "accepted" && r.data.can_act);
        const action =
          !resolved && accepted.length > 0 ? (
            <Btn
              variant="primary"
              size="sm"
              onClick={() =>
                onApplyRun(accepted.map((r) => ({ run_id: runId, card_id: r.data.card.id })))
              }
            >
              {t("review.action.applyRun", { n: accepted.length })}
            </Btn>
          ) : null;
        return (
          <ReviewGroup key={runId} runId={runId} count={group.length} colCount={colCount} action={action}>
            {group.map((row) => (
              <RowView
                key={row.key}
                row={row}
                scoped={scoped}
                resolved={resolved}
                selected={selected.has(row.key)}
                onToggle={onToggle}
                onOpen={onOpen}
                onDecide={onDecide}
              />
            ))}
          </ReviewGroup>
        );
      })}
      {qRows.map((row) => (
        <RowView
          key={row.key}
          row={row}
          scoped={scoped}
          resolved={resolved}
          selected={false}
          onToggle={onToggle}
          onOpen={onOpen}
          onDecide={onDecide}
        />
      ))}
    </tbody>
  );
}

function ReviewGroup({
  runId,
  count,
  colCount,
  action,
  children,
}: {
  runId: string;
  count: number;
  colCount: number;
  action: React.ReactNode;
  children: React.ReactNode;
}) {
  const t = useT();
  return (
    <>
      <tr className="rvw__group">
        <td colSpan={colCount}>
          <span className="rvw__group-tag">{runTag(runId)}</span>
          <span className="rvw__group-count">{t("review.run.count", { n: count })}</span>
          <span className="rvw__group-spacer" />
          {action}
        </td>
      </tr>
      {children}
    </>
  );
}
