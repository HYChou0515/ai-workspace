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

  it("does not wrap the body editor in a <label> (it is not a native control)", () => {
    // The reported symptom: fields typed fine, the body did not. The body's
    // Monaco was the only one in the codebase wrapped in a `<label>` — the YAML
    // editor ten lines above it in this same file uses a `<div>` with the same
    // class, and the six other MonacoEditor call sites use none.
    //
    // A `<label>` promises "click me and I hand focus to my control". Monaco is
    // not a native control — it is divs plus a hidden textarea — so the wrapper
    // intercepts the click and the caret never lands where the user aimed.
    //
    // This assertion is structural because the behaviour it guards cannot be
    // reached from here: this suite MOCKS Monaco as a plain textarea, and a
    // textarea inside a label behaves perfectly. That mock is why the defect
    // survived every green run.
    render(<EntityFileEditor type={issueType} record={record} onSave={vi.fn()} />);

    expect(screen.getByLabelText("body").closest("label")).toBeNull();
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

  it("renders a ref field as a #N-title picker when ref options are supplied", () => {
    const withRef: EntityType = {
      name: "issue",
      records_path: "issues",
      fields: [{ name: "milestone", role: "ref", to: "milestone" }],
      form: [],
    };
    const rec: EntityInstance = { number: 1, type_name: "issue", fields: { milestone: 5 }, body: "", diagnostics: [], version: "v1" };
    render(
      <EntityFileEditor
        type={withRef}
        record={rec}
        onSave={vi.fn()}
        refOptionsFor={(n) => (n === "milestone" ? [{ number: 5, label: "v1.0" }] : undefined)}
      />,
    );
    const sel = screen.getByLabelText("milestone");
    expect(sel.tagName).toBe("SELECT");
    expect((sel as HTMLSelectElement).value).toBe("5");
  });

  it("edits a ref as a picker even with NO targets yet — never a raw number box", () => {
    // The real bug: a fresh project has no milestones, so the editor fell back to
    // a number input ("milestone 只能填數字"). An empty option list must still be
    // the dropdown, so it's clearly a picker (create a milestone → it appears).
    const withRef: EntityType = {
      name: "issue",
      records_path: "issues",
      fields: [{ name: "milestone", role: "ref", to: "milestone" }],
      form: [],
    };
    const rec: EntityInstance = { number: 1, type_name: "issue", fields: {}, body: "", diagnostics: [], version: "v1" };
    render(<EntityFileEditor type={withRef} record={rec} onSave={vi.fn()} refOptionsFor={() => []} />);
    expect(screen.getByLabelText("milestone").tagName).toBe("SELECT");
  });
});
