// @vitest-environment happy-dom
/**
 * Editing a schedule's time holds unsaved work, so its deliberate exits ask
 * once — and only when something changed against what it opened with
 * (CLAUDE.md, "Leaving a modal"). Both cases, as the rule requires.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { ScheduleTimeModal, timeOf } from "./ScheduleTimeModal";

const word = (key: Parameters<typeof translate>[1]) => translate("zh-TW", key);
const REF = { path: "/.workflows/schedules.json", trigger_id: "wui:i:a" };

afterEach(cleanup);

function open(raw: unknown = { every: "weekly", dow: "fri", at: "17:30", tz: "Asia/Taipei", run: "r" }) {
  const onClose = vi.fn();
  renderWithQuery(
    <ScheduleTimeModal raw={raw} rowRef={REF} onSave={vi.fn()} onSaved={vi.fn()} onClose={onClose} />,
  );
  return onClose;
}

describe("ScheduleTimeModal", () => {
  it("opens on the row as written", () => {
    open();
    expect(screen.getByLabelText(word("schedules.edit.every"))).toHaveValue("weekly");
    expect(screen.getByLabelText(word("schedules.edit.dow"))).toHaveValue("fri");
    expect(screen.getByLabelText(word("schedules.edit.at"))).toHaveValue("17:30");
    expect(screen.getByLabelText(word("schedules.edit.tz"))).toHaveValue("Asia/Taipei");
  });

  it("closes at once when nothing changed", () => {
    const onClose = open();
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(word("dirtyClose.prompt"))).toBeNull();
  });

  it("asks once before throwing a changed time away", async () => {
    const onClose = open();
    fireEvent.change(screen.getByLabelText(word("schedules.edit.at")), { target: { value: "08:00" } });
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));

    const ask = await screen.findByRole("dialog", { name: word("dirtyClose.prompt") });
    fireEvent.click(within(ask).getByRole("button", { name: word("dirtyClose.keep") }));
    await waitFor(() => expect(screen.queryByText(word("dirtyClose.prompt"))).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not count switching the period away and back as a change", () => {
    const onClose = open();
    const every = screen.getByLabelText(word("schedules.edit.every"));
    fireEvent.change(every, { target: { value: "monthly" } });
    fireEvent.change(every, { target: { value: "weekly" } });
    fireEvent.click(screen.getByRole("button", { name: word("schedules.cancel") }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("reads a row that leaves fields out with the parser's defaults", () => {
    expect(timeOf({ run: "r" })).toEqual({ every: "daily", n: 15, at: "00:00", dow: "mon", dom: 1, tz: "" });
    expect(timeOf("not a row").every).toBe("daily");
  });
});

