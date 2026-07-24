// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The card Edit modal reuses EntityFileEditor, whose body/YAML ride the lazy
// Monaco stack — swap it for a plain textarea keyed on `ariaLabel`.
vi.mock("../../components/MonacoEditor", () => ({
  MonacoEditor: ({
    value,
    onChange,
    readOnly,
    ariaLabel,
  }: {
    value: string;
    onChange?: (next: string) => void;
    readOnly?: boolean;
    ariaLabel?: string;
  }) => <textarea aria-label={ariaLabel} value={value} disabled={readOnly} onChange={(e) => onChange?.(e.target.value)} />,
}));

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

  describe("card actions menu (#4)", () => {
    it("opens the record's file from the card menu", () => {
      const onOpenRecord = vi.fn();
      board({ entities: [issue(1, { title: "A", status: "open" })], onOpenRecord });
      fireEvent.click(screen.getByRole("button", { name: /card 1 menu/i }));
      fireEvent.click(screen.getByRole("button", { name: "Open file" }));
      expect(onOpenRecord).toHaveBeenCalledWith(1);
    });

    it("edits a card in a modal that saves through the file-editor path", () => {
      const onSave = vi.fn();
      board({ entities: [issue(1, { title: "A", status: "open" })], onSave });
      fireEvent.click(screen.getByRole("button", { name: /card 1 menu/i }));
      fireEvent.click(screen.getByRole("button", { name: "Edit" }));
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByLabelText("title")).toHaveValue("A");
      fireEvent.change(within(dialog).getByLabelText("status"), { target: { value: "done" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(onSave).toHaveBeenCalledWith(1, expect.objectContaining({ status: "done" }), expect.any(String));
      // saving closes the modal.
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("hides Edit for a read-only member but still offers Open file (§E)", () => {
      board({ entities: [issue(1, { title: "A", status: "open" })], canWrite: false, onSave: vi.fn(), onOpenRecord: vi.fn() });
      fireEvent.click(screen.getByRole("button", { name: /card 1 menu/i }));
      expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Open file" })).toBeInTheDocument();
    });
  });

  describe("read-only gate (§E canWrite)", () => {
    it("disables the status select and stops the card from dragging when canWrite is false", () => {
      const onPatch = vi.fn();
      board({ entities: [issue(1, { title: "A", status: "open" })], canWrite: false, onPatch });
      expect(screen.getByLabelText("status")).toBeDisabled();
      // @dnd-kit disables the draggable → the card advertises aria-disabled.
      expect(screen.getByTestId("card-1")).toHaveAttribute("aria-disabled", "true");
    });

    it("keeps the card draggable + status editable by default (canWrite omitted)", () => {
      board({ entities: [issue(1, { title: "A", status: "open" })] });
      expect(screen.getByLabelText("status")).not.toBeDisabled();
      expect(screen.getByTestId("card-1")).toHaveAttribute("aria-disabled", "false");
    });
  });
});
