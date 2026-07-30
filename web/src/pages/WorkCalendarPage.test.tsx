// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkCalendar, WorkCalendarApi } from "../api/workCalendar";
import { mockWorkCalendarApi } from "../api/workCalendar";
import { QueryWrap } from "../test/queryWrapper";
import { WorkCalendarPage } from "./WorkCalendarPage";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

const superuser = vi.fn(() => false);
vi.mock("../hooks/useIsSuperuser", () => ({ useIsSuperuser: () => superuser() }));

function client(over: Partial<WorkCalendarApi> = {}): WorkCalendarApi {
  return { ...mockWorkCalendarApi, ...over };
}

const saved = (overrides: Record<string, string>): WorkCalendar => ({
  workdays: [0, 1, 2, 3, 4],
  overrides,
});

afterEach(() => {
  cleanup();
  superuser.mockReturnValue(false);
});

describe("WorkCalendarPage", () => {
  it("shows the recorded exceptions to anyone", async () => {
    // Everyone can see when the office is closed — only editing is restricted.
    const api = client({ async getCalendar() { return saved({ "2026-08-01": "work" }); } });
    render(<WorkCalendarPage client={api} />);
    expect(await screen.findByDisplayValue("2026-08-01=work")).toBeInTheDocument();
  });

  it("offers no save action to someone who cannot edit", async () => {
    render(<WorkCalendarPage client={client()} />);
    await screen.findByTestId("calendar-overrides");
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
  });

  it("lets a superuser record a make-up workday", async () => {
    superuser.mockReturnValue(true);
    const putCalendar = vi.fn(async (cal: WorkCalendar) => cal);
    render(<WorkCalendarPage client={client({ putCalendar })} />);

    const box = await screen.findByTestId("calendar-overrides");
    await userEvent.clear(box);
    await userEvent.type(box, "2026-08-01=work");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(putCalendar).toHaveBeenCalledWith(
        expect.objectContaining({ overrides: { "2026-08-01": "work" } }),
      ),
    );
  });

  it("refuses to save a line it could not read, and says which", async () => {
    // The failure that matters: a typo saved as nothing at all, leaving the
    // user certain a holiday is recorded when it is not.
    superuser.mockReturnValue(true);
    const putCalendar = vi.fn(async (cal: WorkCalendar) => cal);
    render(<WorkCalendarPage client={client({ putCalendar })} />);

    const box = await screen.findByTestId("calendar-overrides");
    await userEvent.clear(box);
    await userEvent.type(box, "2026-13-99=off");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(await screen.findByTestId("calendar-errors")).toHaveTextContent("2026-13-99");
    expect(putCalendar).not.toHaveBeenCalled();
  });

  it("lets a superuser change which weekdays are working days", async () => {
    superuser.mockReturnValue(true);
    const putCalendar = vi.fn(async (cal: WorkCalendar) => cal);
    render(<WorkCalendarPage client={client({ putCalendar })} />);

    await userEvent.click(await screen.findByRole("checkbox", { name: /saturday/i }));
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(putCalendar).toHaveBeenCalledWith(
        expect.objectContaining({ workdays: [0, 1, 2, 3, 4, 5] }),
      ),
    );
  });
});
