// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { User } from "../../api/types";
import { RoleField, widgetForRole } from "./roleWidget";

afterEach(cleanup);

const users: User[] = [
  { id: "alice", name: "Alice", section: "Eng", email: "a@x", photo_url: "" },
  { id: "bob", name: "Bob", section: "Eng", email: "b@x", photo_url: "" },
];

describe("widgetForRole (the single role→widget table)", () => {
  it("maps each role to its widget kind", () => {
    expect(widgetForRole("text")).toBe("text");
    expect(widgetForRole("status")).toBe("select");
    expect(widgetForRole("actor")).toBe("actor");
    expect(widgetForRole("date")).toBe("date");
    expect(widgetForRole("daterange")).toBe("daterange");
    expect(widgetForRole("progress")).toBe("progress");
    expect(widgetForRole("rank")).toBe("rank");
    expect(widgetForRole("ref")).toBe("ref");
    expect(widgetForRole("backref")).toBe("readonly");
    expect(widgetForRole("rollup")).toBe("readonly");
  });
});

describe("RoleField", () => {
  it("edits an actor as a directory select and commits the chosen user id", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="actor" name="assignee" value="" users={users} onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText("assignee"), { target: { value: "bob" } });
    expect(onCommit).toHaveBeenCalledWith("bob");
  });

  it("keeps an unknown assignee visible so it isn't silently dropped", () => {
    render(<RoleField widget="actor" name="assignee" value="ghost" users={users} onCommit={vi.fn()} />);
    expect(screen.getByLabelText("assignee")).toHaveValue("ghost");
  });

  it("edits a daterange as start + end date inputs and commits start/end", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="daterange" name="span" value="" onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText("span start"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("span end"), { target: { value: "2026-02-01" } });
    expect(onCommit).toHaveBeenLastCalledWith("2026-01-01/2026-02-01");
  });

  it("seeds the daterange inputs from an existing start/end value", () => {
    render(<RoleField widget="daterange" name="span" value="2026-03-01/2026-04-01" onCommit={vi.fn()} />);
    expect(screen.getByLabelText("span start")).toHaveValue("2026-03-01");
    expect(screen.getByLabelText("span end")).toHaveValue("2026-04-01");
  });

  it("renders backref/rollup read-only with no editable control", () => {
    render(<RoleField widget="readonly" name="issues" value={[1, 2]} onCommit={vi.fn()} />);
    expect(screen.queryByLabelText("issues")).not.toBeInTheDocument();
    expect(screen.getByText("1, 2")).toBeInTheDocument();
  });

  it("commits a status select value (§B3 status → dropdown)", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="select" name="status" value="open" values={["open", "done"]} onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    expect(onCommit).toHaveBeenCalledWith("done");
  });

  it("commits an edited numeric progress cell as a number on blur", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="progress" name="progress" value={0} onCommit={onCommit} />);
    const input = screen.getByLabelText("progress");
    fireEvent.change(input, { target: { value: "40" } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledWith(40);
  });

  it("edits a ref as a #N-title picker and commits the target number (§B3)", () => {
    const onCommit = vi.fn();
    render(
      <RoleField widget="ref" name="milestone" value="" refOptions={[{ number: 5, label: "v1.0" }]} onCommit={onCommit} />,
    );
    fireEvent.change(screen.getByLabelText("milestone"), { target: { value: "5" } });
    expect(onCommit).toHaveBeenCalledWith(5);
  });

  it("falls back to a numeric ref input when no options are loaded yet", () => {
    render(<RoleField widget="ref" name="milestone" value={3} onCommit={vi.fn()} />);
    const input = screen.getByLabelText("milestone");
    expect(input).toHaveValue(3);
    expect(input.tagName).toBe("INPUT");
  });
});

describe("number role (#PM auto-schedule P1)", () => {
  it("authors as a number box, so a duration keeps its arithmetic", () => {
    // `progress` is a percent and `rank` is drag order — without a plain number
    // a quantity like "how many days" could only masquerade as text.
    expect(widgetForRole("number")).toBe("number");
  });

  it("commits a typed quantity as a number, not the string the input hands back", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="number" name="exp_days" value={null} onCommit={onCommit} />);
    // The field commits on blur (uncontrolled input, keyed by the committed
    // value) — same contract as every other scalar widget here.
    fireEvent.blur(screen.getByLabelText("exp_days"), { target: { value: "3" } });
    expect(onCommit).toHaveBeenCalledWith(3);
  });
});

describe("half-filled date range (#PM issue-12)", () => {
  it("saves a start with no end instead of silently dropping it", () => {
    // It used to commit only when BOTH ends were set: you picked a start date,
    // the box showed it, and nothing was ever sent — the value was gone on the
    // next load, with no error to explain it.
    const onCommit = vi.fn();
    render(<RoleField widget="daterange" name="span" value={null} onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText("span start"), { target: { value: "2026-07-13" } });
    expect(onCommit).toHaveBeenCalledWith("2026-07-13/");
  });

  it("saves an end with no start too", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="daterange" name="span" value={null} onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText("span end"), { target: { value: "2026-07-15" } });
    expect(onCommit).toHaveBeenCalledWith("/2026-07-15");
  });

  it("clearing both ends still clears the field", () => {
    const onCommit = vi.fn();
    render(<RoleField widget="daterange" name="span" value="2026-07-13/2026-07-15" onCommit={onCommit} />);
    fireEvent.change(screen.getByLabelText("span start"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("span end"), { target: { value: "" } });
    expect(onCommit).toHaveBeenLastCalledWith(null);
  });
});
