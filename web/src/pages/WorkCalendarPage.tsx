/**
 * #615 P1 — the work calendar: which days the office is actually working.
 *
 * Deliberately a plain text list rather than a month grid. The entries are few
 * (a handful of dates a year), they arrive as a published list, and a text box
 * can be pasted into in one go — a calendar widget would be more to build and
 * slower to fill in.
 *
 * A superuser edits it; everyone else reads it. The backend re-checks.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { qk } from "../api/queryKeys";
import { type WorkCalendar, type WorkCalendarApi, workCalendarApi } from "../api/workCalendar";
import { useIsSuperuser } from "../hooks/useIsSuperuser";
import { pxToRem } from "../lib/pxToRem";
import { formatOverrides, parseOverrides } from "../lib/workCalendarText";

/** Monday=0 … Sunday=6, matching the backend's weekday numbering. */
const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

export function WorkCalendarPage({ client = workCalendarApi }: { client?: WorkCalendarApi }) {
  const canEdit = useIsSuperuser();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: qk.workCalendar, queryFn: () => client.getCalendar() });

  const [text, setText] = useState<string | null>(null);
  const [workdays, setWorkdays] = useState<number[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);

  // Seed the draft from the server exactly once it arrives; after that the
  // user's in-progress edits win over a refetch.
  useEffect(() => {
    if (!data) return;
    setText((prev) => (prev === null ? formatOverrides(data.overrides) : prev));
    setWorkdays((prev) => (prev === null ? data.workdays : prev));
  }, [data]);

  const save = useMutation({
    mutationFn: (cal: WorkCalendar) => client.putCalendar(cal),
    onSuccess: (cal) => qc.setQueryData(qk.workCalendar, cal),
  });

  const toggleDay = (day: number) =>
    setWorkdays((prev) => {
      const now = prev ?? [];
      return now.includes(day) ? now.filter((d) => d !== day) : [...now, day].sort((a, b) => a - b);
    });

  const onSave = () => {
    const { overrides, errors: found } = parseOverrides(text ?? "");
    setErrors(found);
    if (found.length > 0) return; // never save a half-understood list
    save.mutate({ workdays: workdays ?? [], overrides });
  };

  if (!data) return <div style={page}>Loading…</div>;
  const days = workdays ?? data.workdays;

  return (
    <div style={page}>
      <header>
        <h1 style={{ margin: 0, fontSize: pxToRem(20) }}>Work calendar</h1>
        <p style={hint}>
          The days people are in the office. Unattended work only runs outside them, so a
          make-up workday recorded here keeps an agent from picking a goal back up while
          you are at your desk.
        </p>
      </header>

      <section style={card}>
        <h2 style={h2}>Working days</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          {WEEKDAYS.map((label, day) => (
            <label key={label} style={dayLabel}>
              <input
                type="checkbox"
                checked={days.includes(day)}
                disabled={!canEdit}
                onChange={() => toggleDay(day)}
              />
              {label}
            </label>
          ))}
        </div>
      </section>

      <section style={card}>
        <h2 style={h2}>Exceptions</h2>
        <p style={hint}>
          One date per line. <code>2026-01-01=off</code> closes a working day;{" "}
          <code>2026-08-01=work</code> opens a weekend one.
        </p>
        <textarea
          data-testid="calendar-overrides"
          aria-label="Calendar exceptions"
          value={text ?? ""}
          readOnly={!canEdit}
          rows={8}
          onChange={(e) => setText(e.target.value)}
          style={box}
        />
        {errors.length > 0 && (
          <ul data-testid="calendar-errors" style={errorList}>
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        )}
      </section>

      {canEdit && (
        <div>
          <button type="button" onClick={onSave} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
          {save.isError && <span style={{ marginLeft: 8, color: "var(--err)" }}>
            Could not save — check the entries above.
          </span>}
        </div>
      )}
    </div>
  );
}

const page: React.CSSProperties = {
  maxWidth: 720,
  margin: "0 auto",
  padding: 24,
  display: "grid",
  gap: 16,
};
const card: React.CSSProperties = {
  border: "1px solid var(--paper-3)",
  borderRadius: 8,
  padding: 12,
  display: "grid",
  gap: 8,
};
const h2: React.CSSProperties = { margin: 0, fontSize: pxToRem(14) };
const hint: React.CSSProperties = {
  color: "var(--text-paper-d)",
  fontSize: pxToRem(13),
  margin: "4px 0 0",
};
const dayLabel: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: pxToRem(13),
};
const box: React.CSSProperties = {
  width: "100%",
  fontFamily: "var(--font-mono)",
  fontSize: pxToRem(13),
  padding: 8,
  borderRadius: 6,
  border: "1px solid var(--paper-3)",
  background: "var(--paper-2)",
  color: "inherit",
};
const errorList: React.CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  color: "var(--err)",
  fontSize: pxToRem(13),
};
