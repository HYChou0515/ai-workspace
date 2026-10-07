/**
 * Move one schedule to a new time (`docs/plan-schedule-overview.md` decision 8)
 * — shared by the schedules overview and the item's Workflows panel, so the
 * two entrances can do the same thing the same way.
 *
 * Only the time: how often, when, which zone. What runs (`run`) and with what
 * (`with`) are not here — changing those is a different act. The fields shown
 * follow the period, and the server keeps only the ones that period reads.
 *
 * A new time is a new schedule to the platform, so the note under the fields
 * says what that costs BEFORE the press (decision 16): it starts from the next
 * time on, its history starts over, and "Run as me" has to be pressed again.
 *
 * Holding unsaved work, every deliberate exit goes through `useDirtyClose`;
 * `dirty` is measured against what the modal opened with (`sameShape`).
 */
import { useId, useState } from "react";

import { type Period, type RowRef, type ScheduleTime, ScheduleActionError } from "../api/schedules";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { type MsgKey, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { sameShape } from "../lib/sameShape";
import { ModalShell } from "./ModalShell";

const PERIODS: Period[] = ["minutes", "hourly", "daily", "weekly", "monthly"];
const DOWS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

/** The row as written, read into the form's shape — the parser's defaults
 * (`daily`, `00:00`) for what it leaves out. */
export function timeOf(raw: unknown): ScheduleTime {
  const r: Record<string, unknown> =
    raw !== null && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const every = PERIODS.includes(r.every as Period) ? (r.every as Period) : "daily";
  return {
    every,
    n: typeof r.n === "number" ? r.n : 15,
    at: typeof r.at === "string" && r.at ? r.at : "00:00",
    dow: typeof r.dow === "string" && r.dow ? r.dow : "mon",
    dom: typeof r.dom === "number" ? r.dom : 1,
    tz: typeof r.tz === "string" ? r.tz : "",
  };
}

export function ScheduleTimeModal({
  raw,
  rowRef,
  onSave,
  onSaved,
  onClose,
}: {
  /** The row as written — the form opens on it. */
  raw: unknown;
  rowRef: RowRef;
  /** Sends the new time; rejects with the server's reason. */
  onSave: (ref: RowRef, time: ScheduleTime) => Promise<void>;
  onSaved: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const [initial] = useState(() => timeOf(raw));
  const [draft, setDraft] = useState<ScheduleTime>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = !sameShape(draft, initial);
  const attemptClose = useDirtyClose(dirty, onClose);
  const set = (patch: Partial<ScheduleTime>) => setDraft((d) => ({ ...d, ...patch }));
  const usesAt = draft.every === "daily" || draft.every === "weekly" || draft.every === "monthly";

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave(rowRef, draft);
      onSaved();
    } catch (e) {
      setError(e instanceof ScheduleActionError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalShell
      onClose={attemptClose}
      labelledBy={titleId}
      data-testid="schedule-time-modal"
      width={420}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}
    >
      <h2 id={titleId} style={{ margin: 0, fontSize: pxToRem(14), fontWeight: 600 }}>
        {t("schedules.edit.title")}
      </h2>
      <label style={field}>
        {t("schedules.edit.every")}
        <select
          className="input"
          value={draft.every}
          onChange={(e) => set({ every: e.target.value as Period })}
          aria-label={t("schedules.edit.every")}
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              {t(`schedules.edit.period.${p}` as MsgKey)}
            </option>
          ))}
        </select>
      </label>
      {draft.every === "minutes" ? (
        <label style={field}>
          {t("schedules.edit.n")}
          <input
            className="input"
            type="number"
            min={1}
            max={60}
            value={draft.n ?? 15}
            onChange={(e) => set({ n: Number(e.target.value) })}
            aria-label={t("schedules.edit.n")}
          />
        </label>
      ) : null}
      {draft.every === "weekly" ? (
        <label style={field}>
          {t("schedules.edit.dow")}
          <select
            className="input"
            value={draft.dow}
            onChange={(e) => set({ dow: e.target.value })}
            aria-label={t("schedules.edit.dow")}
          >
            {DOWS.map((d) => (
              <option key={d} value={d}>
                {t(`schedules.dow.${d}` as MsgKey)}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {draft.every === "monthly" ? (
        <label style={field}>
          {t("schedules.edit.dom")}
          <input
            className="input"
            type="number"
            min={1}
            max={31}
            value={draft.dom ?? 1}
            onChange={(e) => set({ dom: Number(e.target.value) })}
            aria-label={t("schedules.edit.dom")}
          />
        </label>
      ) : null}
      {usesAt ? (
        <label style={field}>
          {t("schedules.edit.at")}
          <input
            className="input"
            type="time"
            value={draft.at}
            onChange={(e) => set({ at: e.target.value })}
            aria-label={t("schedules.edit.at")}
          />
        </label>
      ) : null}
      <label style={field}>
        {t("schedules.edit.tz")}
        <input
          className="input"
          type="text"
          value={draft.tz}
          placeholder="UTC"
          onChange={(e) => set({ tz: e.target.value })}
          aria-label={t("schedules.edit.tz")}
        />
      </label>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("schedules.edit.note")}
      </p>
      {error ? (
        <p role="alert" data-testid="schedule-time-error" style={{ margin: 0, color: "var(--err)", fontSize: pxToRem(12) }}>
          {error}
        </p>
      ) : null}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button type="button" className="btn" data-variant="secondary" data-size="sm" onClick={attemptClose}>
          {t("schedules.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-size="sm"
          data-testid="schedule-time-save"
          disabled={!dirty || busy}
          onClick={() => void submit()}
        >
          {busy ? t("schedules.edit.saving") : t("schedules.edit.save")}
        </button>
      </div>
    </ModalShell>
  );
}

const field: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  fontSize: pxToRem(12),
};
