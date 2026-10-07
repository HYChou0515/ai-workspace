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
 * (else as written, in its own zone), the zone is picked by its name — never
 * typed as an IANA id — and the time is two 24-hour selects, because a time
 * input draws 上午/下午 or AM/PM in some locales while the table reads 24-hour.
 *
 * A new time is a new schedule to the platform, so the note under the fields
 * says what that costs BEFORE the press (decision 16): it starts from the next
 * time on, its history starts over, and "Run as me" has to be pressed again.
 *
 * Holding unsaved work, every deliberate exit goes through `useDirtyClose`;
 * `dirty` is measured against what the modal opened with (`sameShape`).
 */
import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import { type Period, type RowRef, type ScheduleTime, ScheduleActionError } from "../api/schedules";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { type MsgKey, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { sameShape } from "../lib/sameShape";
import { DOWS, moveTime, rowTime, subDailyMoves, validZone, zoneName } from "../lib/scheduleTime";
import { useViewerClock } from "../lib/viewerClock";
import { ModalShell } from "./ModalShell";

const PERIODS: Period[] = ["minutes", "hourly", "daily", "weekly", "monthly"];
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
    // "9:05" is a time the sweep accepts: padded, never replaced by 00:00
    // (saving that would move the schedule — review round 1).
    at: /^\d{1,2}:\d{2}$/.test(written.at) ? written.at.padStart(5, "0") : "00:00",
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

/** Every zone the browser knows, by name — the viewer's own first, then the
 * one the row is in, then UTC, then the rest named with their city (several
 * share a name: 中歐時間 is Berlin and Paris). */
function useZoneOptions(viewer: string, current: string, locale: string, t: ReturnType<typeof useT>) {
  return useMemo(() => {
    const first: { value: string; label: string }[] = [
      { value: viewer, label: t("schedules.edit.yourZone", { zone: zoneName(viewer, locale) }) },
    ];
    for (const zone of [current, "UTC"]) {
      if (zone && !first.some((o) => o.value === zone)) {
        first.push({ value: zone, label: validZone(zone) ? zoneName(zone, locale) : zone });
      }
    }
    const all =
      typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    const rest = all
      .filter((zone) => !first.some((o) => o.value === zone))
      .map((zone) => ({
        value: zone,
        label: `${zoneName(zone, locale)}（${zone.slice(zone.lastIndexOf("/") + 1).replace(/_/g, " ")}）`,
      }));
    return { first, rest };
  }, [viewer, current, locale, t]);
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
  onSave: (ref: RowRef, time: ScheduleTime) => Promise<void>;
  onSaved: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const clock = useViewerClock();
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [initial] = useState(() => timeOf(raw, clock.viewer, nextMs ?? clock.now));
  const [draft, setDraft] = useState<ScheduleTime>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = !sameShape(draft, initial);
  const attemptClose = useDirtyClose(dirty, onClose);
  const set = (patch: Partial<ScheduleTime>) => setDraft((d) => ({ ...d, ...patch }));
  const usesAt = draft.every === "daily" || draft.every === "weekly" || draft.every === "monthly";
  const [hh, mm] = (draft.at ?? "00:00").split(":");
  const zones = useZoneOptions(clock.viewer, initial.tz ?? "", clock.locale, t);

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
      <h2
        id={titleId}
        ref={titleRef}
        tabIndex={-1}
        style={{ margin: 0, fontSize: pxToRem(14), fontWeight: 600, outline: "none" }}
      >
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
      <label style={field}>
        {t("schedules.edit.tz")}
        <select
          className="input"
          value={draft.tz}
          onChange={(e) => set({ tz: e.target.value })}
          aria-label={t("schedules.edit.tz")}
        >
          {zones.first.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
          {zones.rest.length > 0 ? (
            <optgroup label={t("schedules.edit.otherZones")}>
              {zones.rest.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
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
