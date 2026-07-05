// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The body + raw-YAML surfaces ride the lazy Monaco stack; swap it for a plain
// textarea keyed on `ariaLabel` so the editor is drivable without Monaco.
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
import { EntityFileEditor } from "./EntityFileEditor";

afterEach(cleanup);

const issueType: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "done"] },
    { name: "issues", role: "backref", from: "issue.milestone" },
  ],
  form: [],
};

const record: EntityInstance = {
  number: 5,
  type_name: "issue",
  fields: { title: "A", status: "open" },
  body: "orig body",
  diagnostics: [],
  version: "v1",
};

describe("EntityFileEditor (§C2)", () => {
  it("renders a form control per settable field and excludes compute-on-read", () => {
    render(<EntityFileEditor type={issueType} record={record} onSave={vi.fn()} />);
    expect(screen.getByLabelText("title")).toHaveValue("A");
    expect(screen.getByLabelText("status")).toHaveValue("open");
    // backref is compute-on-read → not an editable form field.
    expect(screen.queryByLabelText("issues")).not.toBeInTheDocument();
  });

  it("saves the frontmatter patch + body through onSave (§B1)", () => {
    const onSave = vi.fn();
    render(<EntityFileEditor type={issueType} record={record} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    fireEvent.change(screen.getByLabelText("body"), { target: { value: "new body" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ title: "A", status: "done" }), "new body");
  });

  it("toggles frontmatter to raw YAML and saves the parsed fields", () => {
    const onSave = vi.fn();
    render(<EntityFileEditor type={issueType} record={record} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: /yaml/i }));
    fireEvent.change(screen.getByLabelText("frontmatter yaml"), { target: { value: "title: Z\nstatus: done\n" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ title: "Z", status: "done" }), "orig body");
  });

  it("blocks save and flags invalid YAML instead of writing garbage (§D)", () => {
    render(<EntityFileEditor type={issueType} record={record} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /yaml/i }));
    fireEvent.change(screen.getByLabelText("frontmatter yaml"), { target: { value: "title: [unclosed" } });
    expect(screen.getByText(/invalid yaml/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("disables save when read-only (non-member, §E)", () => {
    render(<EntityFileEditor type={issueType} record={record} canWrite={false} onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
