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
});
