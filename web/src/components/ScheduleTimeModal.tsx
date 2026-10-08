/**
 * Move one schedule to a new time (`docs/plan-schedule-overview.md` decision 8)
 * — shared by the schedules overview and the item's Workflows panel, so the
 * two entrances can do the same thing the same way.
 *
 * Only the time: how often, when, which zone. What runs (`run`) and with what
 * (`with`) are not here — changing those is a different act. The fields shown
 * follow the period, and the server keeps only the ones that period reads.
 *
 * On the VIEWER's clock (`docs/plan-schedule-overview-polish.md` decision 6):
 * the form opens on the row moved into the viewer's zone when it moves cleanly
 * (else as written, in its own zone), says that zone in grey by its name — it
 * is not a choice: nobody needs to pick a zone to move a time — and saves the
 * time in it. The time is two 24-hour selects, because a time input draws
 * 上午/下午 or AM/PM in some locales while the table reads 24-hour.
 *
 * A new time is a new schedule to the platform, so the note under the fields
 * says what that costs BEFORE the press (decision 16): it starts from the next
 * time on, its history starts over, and "Run as me" has to be pressed again.
 *
 * Two modes (`docs/plan-schedule-cron.md` decision 4): 簡單 — the fields above —
 * and cron, a text box with the library's words under it. A row opens in its own
 * kind; switching 簡單 → cron fills the same time as a cron, cron → 簡單 carries a
 * cron the fields can say and otherwise starts over, saying the cron will go.
 * Saving writes the mode on screen, `every` fields or `cron`, never both.
 *
 * Holding unsaved work, every deliberate exit goes through `useDirtyClose`;
 * `dirty` is measured against what the modal opened with (`sameShape`).
 */
import { useId, useLayoutEffect, useRef, useState } from "react";

import { type Period, type RowRef, type ScheduleTime, ScheduleActionError } from "../api/schedules";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { type MsgKey, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { sameShape } from "../lib/sameShape";
import {
  DOWS,
  cronOf,
  cronWords,
  moveTime,
  parseAt,
  rowTime,
  subDailyMoves,
  validZone,
  zoneName,
} from "../lib/scheduleTime";
import { useViewerClock } from "../lib/viewerClock";
import { ModalShell } from "./ModalShell";

const PERIODS: Period[] = ["minutes", "hourly", "daily", "weekly", "monthly"];

/** A row's `at` as the form's `HH:MM`; the parser's 00:00 for one it refuses. */
function atText(at: string): string {
  const parsed = parseAt(at);
  if (!parsed) return "00:00";
  return `${String(parsed.hh).padStart(2, "0")}:${String(parsed.mm).padStart(2, "0")}`;
}
const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
const MINUTES = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, "0"));

/** The row as written, read into the form's shape on the viewer's clock — the
 * parser's defaults (`daily`, `00:00`, UTC) for what it leaves out. A daily /
 * weekly / monthly time is moved into `viewer` through its next occurrence
 * (`refMs`) when it moves cleanly; one that does not stays in its own zone. A
 * minutes / hourly row opens in the viewer's zone when it fires on the same
 * minutes there (`subDailyMoves`), else in its own. */
export function timeOf(raw: unknown, viewer: string, refMs: number): ScheduleTime {
  const r: Record<string, unknown> =
    raw !== null && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const written = rowTime(raw);
  const every = PERIODS.includes(written.every as Period) ? (written.every as Period) : "daily";
  const form: ScheduleTime = {
    every,
    n: typeof r.n === "number" ? r.n : 15,
    // "9:05" and "9:5" are times the sweep accepts (`parseAt`): written out
    // padded, never replaced by 00:00 — saving that would move the schedule
    // (review rounds 1 and 2).
    at: atText(written.at),
    dow: written.dow && (DOWS as readonly string[]).includes(written.dow) ? written.dow : "mon",
    dom: typeof r.dom === "number" ? r.dom : 1,
    tz: written.tz,
  };
  if (every === "minutes" || every === "hourly") {
    return subDailyMoves({ every, n: form.n ?? 0, tz: written.tz }, viewer, refMs) ? { ...form, tz: viewer } : form;
  }
  const moved = moveTime({ ...written, every, at: form.at ?? "00:00", dow: form.dow ?? "mon", dom: form.dom ?? 1 }, viewer, refMs);
  if (moved === null) return form;
  // Only a weekly row's weekday moves; a daily one's occurrence has a weekday
  // too, which is not the row's.
  return { ...form, at: moved.at, dow: every === "weekly" ? moved.dow : form.dow, tz: viewer };
}

/** cron's day-of-week numbers (0 and 7 are Sunday). */
const CRON_DOW: Record<string, string> = { sun: "0", mon: "1", tue: "2", wed: "3", thu: "4", fri: "5", sat: "6" };

/** The simple form's time as the same cron, in the same zone. */
export function simpleToCron(time: ScheduleTime): string {
  const parsed = parseAt(time.at ?? "00:00") ?? { hh: 0, mm: 0 };
  const { hh, mm } = parsed;
  switch (time.every) {
    case "minutes": {
      const n = time.n ?? 1;
      if (n <= 1) return "* * * * *";
      // Every 60 minutes is the top of the hour; `*/60` is not a minute.
      return n >= 60 ? "0 * * * *" : `*/${n} * * * *`;
    }
    case "hourly":
      return "0 * * * *";
    case "weekly":
      return `${mm} ${hh} * * ${CRON_DOW[time.dow ?? "mon"] ?? "1"}`;
    case "monthly":
      return `${mm} ${hh} ${time.dom ?? 1} * *`;
    default:
      return `${mm} ${hh} * * *`;
  }
}

/** A cron as the simple form's fields, or `null` when the fields cannot say it
 * (a range of days, a step of hours, a month). The zone is the caller's. */
export function cronToSimple(cron: string): Omit<ScheduleTime, "tz"> | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [mi, h, dom, mon, dow] = fields;
  const num = (s: string, max: number) => (/^\d+$/.test(s) && Number(s) <= max ? Number(s) : null);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (mon !== "*") return null;
  if (dom === "*" && dow === "*" && h === "*") {
    if (mi === "*") return { every: "minutes", n: 1 };
    if (mi === "0") return { every: "hourly" };
    const step = /^\*\/(\d+)$/.exec(mi);
    return step && 60 % Number(step[1]) === 0 ? { every: "minutes", n: Number(step[1]) } : null;
  }
  const H = num(h, 23);
  const Mi = num(mi, 59);
  if (H === null || Mi === null) return null;
  const at = `${pad(H)}:${pad(Mi)}`;
  if (dom === "*" && dow === "*") return { every: "daily", at };
  if (dom === "*" && /^[0-7]$/.test(dow)) {
    const day = Object.keys(CRON_DOW).find((d) => CRON_DOW[d] === String(Number(dow) % 7));
    return { every: "weekly", dow: day, at };
  }
  const D = num(dom, 31);
  if (dow === "*" && D !== null && D >= 1) return { every: "monthly", dom: D, at };
  return null;
}

type Mode = "simple" | "cron";

/** The form's whole state: which mode, the simple fields, the cron text. The
 * simple fields hold the zone for both modes. `snapshot` is the simple fields as
 * the last switch to 簡單 left them — switching back to cron with them untouched
 * keeps the cron as typed instead of overwriting it from the fields. */
type FormState = {
  mode: Mode;
  simple: ScheduleTime;
  cron: string;
  snapshot: ScheduleTime | null;
  /** The last switch to 簡單 could not carry the cron over. */
  dropped: boolean;
};

const blankSimple = (tz: string): ScheduleTime => ({ every: "daily", n: 15, at: "00:00", dow: "mon", dom: 1, tz });

function openState(raw: unknown, viewer: string, refMs: number): FormState {
  const cron = cronOf(raw);
  if (cron === null) {
    return { mode: "simple", simple: timeOf(raw, viewer, refMs), cron: "", snapshot: null, dropped: false };
  }
  // Not moved onto the viewer's clock (decision 3): a cron opens in its zone.
  const tz = rowTime(raw).tz;
  const carried = cronToSimple(cron);
  return { mode: "cron", simple: { ...blankSimple(tz), ...carried, tz }, cron, snapshot: null, dropped: false };
}

/** What Save sends — and what "changed" is measured on. */
function payloadOf(state: FormState): ScheduleTime | { cron: string; tz: string } {
  if (state.mode === "cron") return { cron: state.cron.trim(), tz: state.simple.tz ?? "" };
  return state.simple;
}

export function ScheduleTimeModal({
  raw,
  nextMs,
  rowRef,
  onSave,
  onSaved,
  onClose,
}: {
  /** The row as written — the form opens on it. */
  raw: unknown;
  /** When it runs next — which daylight saving its time is moved with. */
  nextMs?: number | null;
  rowRef: RowRef;
  /** Sends the new time; rejects with the server's reason. */
  onSave: (ref: RowRef, time: ScheduleTime | { cron: string; tz: string }) => Promise<void>;
  onSaved: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const clock = useViewerClock();
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [initial] = useState(() => openState(raw, clock.viewer, nextMs ?? clock.now));
  const [state, setState] = useState<FormState>(initial);
  const draft = state.simple;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = !sameShape(payloadOf(state), payloadOf(initial));
  const attemptClose = useDirtyClose(dirty, onClose);
  const set = (patch: Partial<ScheduleTime>) => setState((s) => ({ ...s, simple: { ...s.simple, ...patch } }));
  const switchTo = (mode: Mode) =>
    setState((s) => {
      if (s.mode === mode) return s;
      if (mode === "cron") {
        const kept = s.snapshot !== null && sameShape(s.simple, s.snapshot);
        return { ...s, mode, cron: kept ? s.cron : simpleToCron(s.simple) };
      }
      const carried = cronToSimple(s.cron);
      const tz = s.simple.tz ?? "";
      const simple = carried ? { ...s.simple, ...carried, tz } : blankSimple(tz);
      return { ...s, mode, simple, snapshot: simple, dropped: carried === null };
    });
  // The cron as Save would send it: exactly 5 fields (the backend's rule), and
  // one the library can read — its words, or why not.
  const cronFields = state.cron.trim().split(/\s+/).filter(Boolean).length;
  const cronSaid = cronWords(state.cron, clock.locale);
  const cronProblem =
    state.mode !== "cron"
      ? null
      : cronFields !== 5
        ? t("schedules.edit.cronFields")
        : cronSaid === null
          ? t("schedules.edit.cronUnreadable")
          : null;
  const monthEnd = cronToSimple(state.cron);
  const skipsMonths = state.mode === "cron" && monthEnd?.every === "monthly" && (monthEnd.dom ?? 0) >= 29;
  const usesAt = draft.every === "daily" || draft.every === "weekly" || draft.every === "monthly";
  const [hh, mm] = (draft.at ?? "00:00").split(":");
  // The zone the time is read in — shown, not picked; saved as it is.
  const zoneTz = draft.tz || "UTC";
  const zoneWords = validZone(zoneTz) ? zoneName(zoneTz, clock.locale) : zoneTz;

  // Focus the title, not the first field: a field's focus ring is the accent
  // colour and read as an error on a form nobody has touched yet. ModalShell
  // leaves focus where the content put it (it runs after this).
  useLayoutEffect(() => {
    titleRef.current?.focus();
  }, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave(rowRef, payloadOf(state));
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
      <h2
        id={titleId}
        ref={titleRef}
        tabIndex={-1}
        style={{ margin: 0, fontSize: pxToRem(14), fontWeight: 600, outline: "none" }}
      >
        {t("schedules.edit.title")}
      </h2>
      <div role="radiogroup" aria-label={t("schedules.edit.mode")} style={{ display: "flex", gap: 6 }}>
        {(["simple", "cron"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={state.mode === m}
            className="btn"
            data-variant={state.mode === m ? "primary" : "secondary"}
            data-size="sm"
            onClick={() => switchTo(m)}
          >
            {t(`schedules.edit.mode.${m}` as MsgKey)}
          </button>
        ))}
      </div>
      {state.mode === "cron" ? (
        <label style={field}>
          cron
          <input
            className="input"
            type="text"
            value={state.cron}
            spellCheck={false}
            onChange={(e) => setState((s) => ({ ...s, cron: e.target.value }))}
            aria-label="cron"
            placeholder="0 9 * * 1-5"
            style={{ fontFamily: "var(--font-mono)" }}
          />
          {cronProblem ? (
            <span data-testid="schedule-cron-error" role="alert" style={{ color: "var(--err)" }}>
              {cronProblem}
            </span>
          ) : (
            <span data-testid="schedule-cron-words" style={{ color: "var(--text-paper-d)" }}>
              {cronSaid}
            </span>
          )}
          {skipsMonths ? (
            <span style={{ color: "var(--text-paper-d)" }}>{t("schedules.edit.cronSkipsMonths")}</span>
          ) : null}
        </label>
      ) : null}
      {state.mode === "simple" && state.dropped ? (
        <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
          {t("schedules.edit.replacesCron")}
        </p>
      ) : null}
      {state.mode === "simple" ? (
      <>
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
        <fieldset style={{ ...field, border: 0, padding: 0, margin: 0 }}>
          <legend style={{ padding: 0, marginBottom: 4 }}>{t("schedules.edit.at")}</legend>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <select
              className="input"
              value={hh}
              onChange={(e) => set({ at: `${e.target.value}:${mm}` })}
              aria-label={t("schedules.edit.hour")}
            >
              {HOURS.map((h) => (
                <option key={h} value={h}>
                  {h}
                </option>
              ))}
            </select>
            :
            <select
              className="input"
              value={mm}
              onChange={(e) => set({ at: `${hh}:${e.target.value}` })}
              aria-label={t("schedules.edit.minute")}
            >
              {MINUTES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        </fieldset>
      ) : null}
      </>
      ) : null}
      <p
        data-testid="schedule-time-zone"
        style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}
      >
        {zoneTz === clock.viewer
          ? t("schedules.edit.zoneYours", { zone: zoneWords })
          : t("schedules.edit.zone", { zone: zoneWords })}
      </p>
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
          disabled={!dirty || busy || cronProblem !== null}
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
