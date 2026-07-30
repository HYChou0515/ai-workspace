/**
 * #615 P1 — the deployment's work calendar (which days people are in the office).
 *
 * Read by anyone; written by a superuser only (the backend is the real gate —
 * the page just hides the editor via `useIsSuperuser`). The off-hours sweeper
 * reads the same row, so what is saved here is what decides when an unattended
 * agent may pick a goal back up.
 */

import { apiFetch } from "./http";

export type WorkCalendar = {
  /** Working weekdays, Monday=0 … Sunday=6. */
  workdays: number[];
  /** `YYYY-MM-DD` → `off` (a holiday) | `work` (a make-up workday). */
  overrides: Record<string, string>;
};

export type WorkCalendarApi = {
  getCalendar(): Promise<WorkCalendar>;
  putCalendar(cal: WorkCalendar): Promise<WorkCalendar>;
};

const URL = "/work-calendar";

export const workCalendarApi: WorkCalendarApi = {
  async getCalendar() {
    const r = await apiFetch(URL);
    return (await r.json()) as WorkCalendar;
  },
  async putCalendar(cal) {
    const r = await apiFetch(URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cal),
    });
    return (await r.json()) as WorkCalendar;
  },
};

/** In-memory mock for tests / mock mode — the unconfigured default. */
export const mockWorkCalendarApi: WorkCalendarApi = {
  async getCalendar() {
    return { workdays: [0, 1, 2, 3, 4], overrides: {} };
  },
  async putCalendar(cal) {
    return cal;
  },
};
