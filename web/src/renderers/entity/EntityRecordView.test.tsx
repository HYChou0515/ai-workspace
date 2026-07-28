// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import type { EntityInstance, EntityType } from "../../api/entities";
import { EntityRecordView } from "./EntityRecordView";

afterEach(cleanup);

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "done"] },
    { name: "assignee", role: "actor" },
    { name: "span", role: "daterange" },
    { name: "progress", role: "progress" },
    { name: "milestone", role: "ref", to: "milestone" },
    { name: "rank", role: "rank" },
  ],
  form: [],
};

const users = [{ id: "alice", name: "Alice Chen", section: "", email: "", photo_url: "" }];

const record: EntityInstance = {
  number: 7,
  type_name: "issue",
  fields: {
    title: "The bar stops a day short",
    status: "open",
    assignee: "alice",
    span: "2026-07-13/2026-07-15",
    progress: 40,
    milestone: 2,
    rank: 1.5,
  },
  body: "## Repro\n\nOpen the Timeline and look at `7/15`.\n\n| day | coloured |\n| --- | --- |\n| 7/13 | yes |\n| 7/15 | no |\n",
  diagnostics: [],
};

function view(overrides: Partial<React.ComponentProps<typeof EntityRecordView>> = {}) {
  return render(
    <FileServiceProvider value={investigationFileService("rca", "item-1")}>
      <EntityRecordView
        type={type}
        record={record}
        users={users}
        path="issues/7.md"
        canWrite
        onEdit={vi.fn()}
        refOptionsFor={() => [{ number: 2, label: "M2 — hardening" }]}
        {...overrides}
      />
    </FileServiceProvider>,
  );
}

describe("EntityRecordView", () => {
  it("renders the body as markdown instead of as source", () => {
    view();
    expect(screen.getByRole("heading", { name: "Repro" })).toBeInTheDocument();
    // A GFM table is the case that reads worst as raw text.
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByText(/^## Repro/)).not.toBeInTheDocument();
  });

  it("is a reading surface — no input boxes to fall into", () => {
    view();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  });

  it("shows each field resolved, not raw", () => {
    view();
    const fields = screen.getByTestId("record-fields");
    expect(within(fields).getByText("Alice Chen")).toBeInTheDocument(); // not "alice"
    expect(within(fields).getByText("M2 — hardening")).toBeInTheDocument(); // not "2"
    expect(within(fields).getByText("40%")).toBeInTheDocument();
    expect(within(fields).getByText("open")).toBeInTheDocument();
  });

  it("leaves the manual-order field out — infrastructure, not content", () => {
    view();
    expect(screen.queryByText("rank")).not.toBeInTheDocument();
    expect(screen.queryByText("1.5")).not.toBeInTheDocument();
  });

  it("says so when there is nothing written yet", () => {
    view({ record: { ...record, body: "" } });
    expect(screen.getByText(/nothing written/i)).toBeInTheDocument();
  });

  it("offers Edit to a writer and withholds it from a reader", () => {
    const onEdit = vi.fn();
    view({ onEdit });
    screen.getByRole("button", { name: /edit/i }).click();
    expect(onEdit).toHaveBeenCalled();
    cleanup();
    view({ canWrite: false });
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });
});
