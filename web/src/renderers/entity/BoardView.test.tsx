// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { BoardView } from "./BoardView";
import type { EntityViewProps, ViewSpec } from "./types";

afterEach(cleanup);

const issueType: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "status", role: "status", values: ["open", "in_progress", "done"] },
    { name: "assignee", role: "actor" },
    { name: "progress", role: "progress" },
    { name: "due", role: "date" },
  ],
  form: [],
};
const users: User[] = [{ id: "alice", name: "Alice", section: "Eng", email: "a@x", photo_url: "" }];
const issue = (n: number, fields: Record<string, unknown>): EntityInstance => ({
  number: n,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});
const boardSpec: ViewSpec = {
  view: "board",
  entity: "issue",
  group_by: "status",
  card: { title: "title", badges: ["assignee", "progress", "due"] },
};

function board(props: Partial<EntityViewProps>) {
  return render(<BoardView spec={boardSpec} type={issueType} entities={[]} onCreate={vi.fn()} onPatch={vi.fn()} {...props} />);
}

describe("BoardView (#451)", () => {
  it("keeps the status select as an accessible fallback that moves a card", () => {
    const onPatch = vi.fn();
    board({ entities: [issue(1, { title: "A", status: "open" })], onPatch });
    expect(screen.getByTestId("col-done")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    expect(onPatch).toHaveBeenCalledWith(1, { status: "done" });
  });

  it("shows an out-of-vocab status in its own degraded column, card still visible (§D)", () => {
    board({ entities: [issue(1, { title: "A", status: "weird" })] });
    expect(screen.getByTestId("col-weird")).toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("renders an actor badge as the directory name", () => {
    board({ entities: [issue(1, { title: "A", status: "open", assignee: "alice" })], users });
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("renders a progress badge as a labelled percentage bar", () => {
    board({ entities: [issue(1, { title: "A", status: "open", progress: 40 })] });
    expect(screen.getByLabelText(/progress 40%/i)).toBeInTheDocument();
  });

  it("marks each card draggable (@dnd-kit)", () => {
    board({ entities: [issue(1, { title: "A", status: "open" })] });
    expect(screen.getByTestId("card-1")).toHaveAttribute("aria-roledescription", "draggable");
  });
});
