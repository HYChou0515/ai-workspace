// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The editor's body + raw-YAML surfaces ride the lazy Monaco stack; swap it for a
// plain textarea so the pane is drivable without Monaco (same idiom as the
// EntityFileEditor tests).
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
  }) => (
    <textarea
      aria-label={ariaLabel}
      value={value}
      disabled={readOnly}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

import type { EntityInstance, EntityType } from "../../api/entities";
import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { EntityRecordPane } from "./EntityRecordPane";

afterEach(cleanup);

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "done"] },
    { name: "done_count", role: "rollup", over: "children", agg: "count" },
  ],
  form: [],
};

const record: EntityInstance = {
  number: 7,
  type_name: "issue",
  fields: { title: "The bar stops a day short", status: "open", done_count: 3 },
  body: "## Repro\n\nOpen the Timeline.\n",
  diagnostics: [],
};

function pane(overrides: Partial<React.ComponentProps<typeof EntityRecordPane>> = {}) {
  const onSave = vi.fn();
  const utils = render(
    <FileServiceProvider value={investigationFileService("rca", "item-1")}>
      <EntityRecordPane type={type} record={record} path="issues/7.md" canWrite onSave={onSave} {...overrides} />
    </FileServiceProvider>,
  );
  return { ...utils, onSave };
}

describe("EntityRecordPane", () => {
  it("opens in the READING state, not the edit form", () => {
    pane();
    // The reading view shows the record's fields as a definition list…
    expect(screen.getByTestId("record-fields")).toBeInTheDocument();
    // …and offers Edit rather than Save.
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("flips to the edit form on Edit, and back to reading after a save", () => {
    const { onSave } = pane();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    // A text widget is uncontrolled and commits on BLUR, so typing alone doesn't
    // reach the form's state — leaving the field is what stages the value.
    fireEvent.change(screen.getByLabelText("title"), { target: { value: "Renamed" } });
    fireEvent.blur(screen.getByLabelText("title"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    const [patch, body] = onSave.mock.calls[0];
    expect(patch).toMatchObject({ title: "Renamed" });
    // Compute-on-read fields are never written back.
    expect(patch).not.toHaveProperty("done_count");
    expect(body).toContain("## Repro");
    // The write is optimistic, so we land back on the reading view — which is
    // also where a 409 banner has somewhere to be seen.
    expect(screen.getByTestId("record-fields")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("leaves the form without saving on Cancel", () => {
    const { onSave } = pane();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId("record-fields")).toBeInTheDocument();
  });

  it("offers no way in for a read-only member", () => {
    pane({ canWrite: false });
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});
