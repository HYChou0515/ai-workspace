// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SortRule, ViewConfig } from "./types";
import { ViewSettingsPanel } from "./ViewSettingsPanel";

afterEach(cleanup);

function config(over: Partial<ViewConfig> = {}): ViewConfig {
  return {
    fieldOptions: [
      { name: "title", label: "title" },
      { name: "status", label: "status" },
      { name: "due", label: "due" },
    ],
    hidden: [],
    onToggleField: vi.fn(),
    groupBy: "",
    groupOptions: [{ name: "status", label: "status" }],
    onGroupBy: vi.fn(),
    sort: [],
    sortOptions: [
      { name: "status", label: "status" },
      { name: "title", label: "title" },
      { name: "due", label: "due" },
    ],
    onSetSort: vi.fn(),
    dirty: false,
    onSave: vi.fn(),
    onReset: vi.fn(),
    ...over,
  };
}

const open = () => fireEvent.click(screen.getByRole("button", { name: "view settings" }));

describe("ViewSettingsPanel", () => {
  it("opens + closes the popover from the View button", () => {
    render(<ViewSettingsPanel config={config()} />);
    expect(screen.queryByRole("dialog", { name: "view settings" })).not.toBeInTheDocument();
    open();
    expect(screen.getByRole("dialog", { name: "view settings" })).toBeInTheDocument();
    open();
    expect(screen.queryByRole("dialog", { name: "view settings" })).not.toBeInTheDocument();
  });

  it("shows a Skip weekends toggle when the config carries it, and toggling calls onToggleSkipWeekends", () => {
    const onToggleSkipWeekends = vi.fn();
    render(<ViewSettingsPanel config={config({ skipWeekends: false, onToggleSkipWeekends })} />);
    open();
    const cb = screen.getByRole("checkbox", { name: "Skip weekends" });
    expect(cb).not.toBeChecked();
    fireEvent.click(cb);
    expect(onToggleSkipWeekends).toHaveBeenCalledWith(true);
  });

  it("a gantt (skip-only) config hides Group by / Sort / Fields, showing just Working days", () => {
    render(
      <ViewSettingsPanel
        config={config({ groupOptions: [], sortOptions: [], fieldOptions: [], skipWeekends: true, onToggleSkipWeekends: vi.fn() })}
      />,
    );
    open();
    expect(screen.getByRole("checkbox", { name: "Skip weekends" })).toBeChecked();
    expect(screen.queryByText("Group by")).not.toBeInTheDocument();
    expect(screen.queryByText("Sort by")).not.toBeInTheDocument();
    expect(screen.queryByText("Fields")).not.toBeInTheDocument();
  });

  it("shows a People display select when the config carries it, and changing it calls onSetAssigneeDisplay", () => {
    const onSetAssigneeDisplay = vi.fn();
    render(<ViewSettingsPanel config={config({ assigneeDisplay: "avatar", onSetAssigneeDisplay })} />);
    open();
    const sel = screen.getByRole("combobox", { name: "people display" });
    expect(sel).toHaveValue("avatar");
    fireEvent.change(sel, { target: { value: "name" } });
    expect(onSetAssigneeDisplay).toHaveBeenCalledWith("name");
  });

  it("toggles a field's visibility", () => {
    const onToggleField = vi.fn();
    render(<ViewSettingsPanel config={config({ onToggleField })} />);
    open();
    fireEvent.click(screen.getByLabelText("show status"));
    expect(onToggleField).toHaveBeenCalledWith("status");
  });

  it("checks a field only when it is not hidden", () => {
    render(<ViewSettingsPanel config={config({ hidden: ["status"] })} />);
    open();
    expect(screen.getByLabelText("show title")).toBeChecked();
    expect(screen.getByLabelText("show status")).not.toBeChecked();
  });

  it("changes the group-by field", () => {
    const onGroupBy = vi.fn();
    render(<ViewSettingsPanel config={config({ onGroupBy })} />);
    open();
    fireEvent.change(screen.getByLabelText("group by"), { target: { value: "status" } });
    expect(onGroupBy).toHaveBeenCalledWith("status");
  });

  it("adds a sort tier with the first unused field, ascending", () => {
    const onSetSort = vi.fn();
    render(<ViewSettingsPanel config={config({ onSetSort })} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "add sort" }));
    expect(onSetSort).toHaveBeenCalledWith([{ field: "status", dir: "asc" }]);
  });

  it("adds a SECOND tier skipping the field already used (multi-level)", () => {
    const onSetSort = vi.fn();
    render(<ViewSettingsPanel config={config({ sort: [{ field: "status", dir: "asc" }], onSetSort })} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "add sort" }));
    expect(onSetSort).toHaveBeenCalledWith([
      { field: "status", dir: "asc" },
      { field: "title", dir: "asc" },
    ]);
  });

  it("caps the sort tiers at 3 (hides Add sort)", () => {
    const sort: SortRule[] = [
      { field: "status", dir: "asc" },
      { field: "title", dir: "asc" },
      { field: "due", dir: "asc" },
    ];
    render(<ViewSettingsPanel config={config({ sort })} />);
    open();
    expect(screen.queryByRole("button", { name: "add sort" })).not.toBeInTheDocument();
  });

  it("flips a tier's direction and removes a tier", () => {
    const onSetSort = vi.fn();
    render(<ViewSettingsPanel config={config({ sort: [{ field: "status", dir: "asc" }], onSetSort })} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "sort direction 1" }));
    expect(onSetSort).toHaveBeenCalledWith([{ field: "status", dir: "desc" }]);
    fireEvent.click(screen.getByRole("button", { name: "remove sort 1" }));
    expect(onSetSort).toHaveBeenCalledWith([]);
  });

  it("shows Save + Reset only when dirty", () => {
    const onSave = vi.fn();
    const onReset = vi.fn();
    const { rerender } = render(<ViewSettingsPanel config={config({ dirty: false })} />);
    open();
    expect(screen.queryByRole("button", { name: "Save to view" })).not.toBeInTheDocument();
    rerender(<ViewSettingsPanel config={config({ dirty: true, onSave, onReset })} />);
    fireEvent.click(screen.getByRole("button", { name: "Save to view" }));
    expect(onSave).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onReset).toHaveBeenCalled();
  });
});

describe("colour source (#690 P3)", () => {
  it("offers no colour section to a view that cannot use one", () => {
    // Table and board colour their chips from the field itself; only the gantt
    // has a bar with one colour to spend.
    render(<ViewSettingsPanel config={config()} />);
    open();

    expect(screen.queryByLabelText("colour by")).not.toBeInTheDocument();
  });

  it("lets the user pick what the colour means, and persists it", () => {
    const onSetColorBy = vi.fn();
    render(
      <ViewSettingsPanel
        config={config({
          colorBy: "",
          colorByOptions: [
            { name: "status", label: "status" },
            { name: "urgency", label: "urgency" },
          ],
          onSetColorBy,
        })}
      />,
    );
    open();

    fireEvent.change(screen.getByLabelText("colour by"), { target: { value: "urgency" } });

    expect(onSetColorBy).toHaveBeenCalledWith("urgency");
  });

  it("offers turning it off again", () => {
    // The one colour bars had before is a real answer, not an absence — a view
    // that cannot get back to it is a one-way door.
    const onSetColorBy = vi.fn();
    render(
      <ViewSettingsPanel
        config={config({
          colorBy: "urgency",
          colorByOptions: [{ name: "urgency", label: "urgency" }],
          onSetColorBy,
        })}
      />,
    );
    open();

    fireEvent.change(screen.getByLabelText("colour by"), { target: { value: "" } });

    expect(onSetColorBy).toHaveBeenCalledWith("");
  });

  describe("Time axis (#690 P9)", () => {
    // Present only for a gantt whose view carries a week rule — without one the
    // axis has no week to show and these three would be knobs wired to nothing.
    const axis = (over = {}) => ({
      alwaysWeek: false,
      onToggleAlwaysWeek: vi.fn(),
      weekday: "number",
      onSetWeekday: vi.fn(),
      dayOfMonth: "hidden",
      onSetDayOfMonth: vi.fn(),
      ...over,
    });

    it("is absent when the view has no week rule to show", () => {
      render(<ViewSettingsPanel config={config()} />);
      open();
      expect(screen.queryByLabelText("always show week")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("weekday format")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("day of month")).not.toBeInTheDocument();
    });

    it("keeps the week visible at the widest zoom when asked", () => {
      const a = axis();
      render(<ViewSettingsPanel config={config(a)} />);
      open();
      const cb = screen.getByRole("checkbox", { name: "always show week" });
      expect(cb).not.toBeChecked();
      fireEvent.click(cb);
      expect(a.onToggleAlwaysWeek).toHaveBeenCalledWith(true);
    });

    it("writes the weekday as digits or as names, digits being the default", () => {
      const a = axis();
      render(<ViewSettingsPanel config={config(a)} />);
      open();
      const sel = screen.getByLabelText("weekday format") as HTMLSelectElement;
      expect(sel.value).toBe("number");
      fireEvent.change(sel, { target: { value: "short" } });
      expect(a.onSetWeekday).toHaveBeenCalledWith("short");
    });

    it("offers the day of the month as a second line, on hover, or not at all", () => {
      const a = axis({ dayOfMonth: "hover" });
      render(<ViewSettingsPanel config={config(a)} />);
      open();
      const sel = screen.getByLabelText("day of month") as HTMLSelectElement;
      expect(sel.value).toBe("hover");
      expect([...sel.options].map((o) => o.value)).toEqual(["hidden", "always", "hover"]);
      fireEvent.change(sel, { target: { value: "always" } });
      expect(a.onSetDayOfMonth).toHaveBeenCalledWith("always");
    });
  });
});