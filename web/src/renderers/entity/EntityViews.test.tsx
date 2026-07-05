// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityHealthFinding, EntityInstance, EntityType } from "../../api/entities";
import {
  EntityViewBody,
  fieldText,
  HealthView,
  parseSpan,
  parseViewSpec,
  type ViewSpec,
} from "./EntityViews";
import { buildRefIndex } from "./refTraversal";

const issueType: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "in_progress", "done"] },
    { name: "progress", role: "progress" },
    { name: "span", role: "daterange" },
  ],
  form: [
    { name: "title", widget: "text", required: true },
    { name: "status", widget: "select", required: false, values: ["open", "done"] },
  ],
};

function issue(number: number, fields: Record<string, unknown>): EntityInstance {
  return { number, type_name: "issue", fields, body: "", diagnostics: [] };
}

const tableSpec: ViewSpec = { view: "table", entity: "issue", columns: ["title", "status", "progress"] };

afterEach(cleanup);

describe("parseViewSpec", () => {
  it("parses a well-formed view", () => {
    expect(parseViewSpec("view: table\nentity: issue\n")).toMatchObject({ view: "table", entity: "issue" });
  });
  it("rejects malformed YAML", () => {
    expect(parseViewSpec("view: [unclosed")).toBeNull();
  });
  it("rejects an unknown view kind", () => {
    expect(parseViewSpec("view: pie\nentity: issue\n")).toBeNull();
  });
  it("rejects a record-bound spec with no entity", () => {
    expect(parseViewSpec("view: table\n")).toBeNull();
  });
  it("accepts a health spec with no entity (it's cross-type)", () => {
    expect(parseViewSpec("view: health\ntitle: Health\n")).toMatchObject({ view: "health" });
  });
});

describe("HealthView", () => {
  const findings: EntityHealthFinding[] = [
    { type_name: "issue", number: 2, level: "error", message: "no frontmatter" },
    { type_name: "issue", number: 3, level: "warning", message: "status off", field: "status" },
  ];

  it("lists findings with their level, record, and message", () => {
    render(<HealthView title="Health" findings={findings} />);
    expect(screen.getByText(/1 error, 1 warning/)).toBeInTheDocument();
    expect(screen.getByText("no frontmatter")).toBeInTheDocument();
    expect(screen.getByText(/status off/)).toBeInTheDocument();
  });

  it("shows an all-clear when there are no findings", () => {
    render(<HealthView findings={[]} />);
    expect(screen.getByText(/All records are healthy/)).toBeInTheDocument();
  });
});

describe("parseSpan", () => {
  it("parses a `start/end` string", () => {
    expect(parseSpan("2026-01-01/2026-02-01")).toEqual({
      start: Date.parse("2026-01-01"),
      end: Date.parse("2026-02-01"),
    });
  });
  it("parses a two-element list and a {start,end} object", () => {
    expect(parseSpan(["2026-01-01", "2026-02-01"])).not.toBeNull();
    expect(parseSpan({ start: "2026-01-01", end: "2026-02-01" })).not.toBeNull();
  });
  it("returns null for junk or a reversed range", () => {
    expect(parseSpan("nope")).toBeNull();
    expect(parseSpan("2026-02-01/2026-01-01")).toBeNull();
  });
});

describe("fieldText", () => {
  it("joins arrays and blanks nullish", () => {
    expect(fieldText([1, 2])).toBe("1, 2");
    expect(fieldText(null)).toBe("");
    expect(fieldText("x")).toBe("x");
  });
});

describe("TableView", () => {
  it("renders a column per spec column plus the record number", () => {
    render(
      <EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { title: "Login broken", status: "open" })]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.getByRole("columnheader", { name: "title" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "status" })).toBeInTheDocument();
    // scalar cells are inline-editable inputs — the value lives on the input.
    expect(screen.getByLabelText("title")).toHaveValue("Login broken");
  });

  it("commits a status change through onPatch (the update write path)", () => {
    const onPatch = vi.fn();
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { status: "open" })]} onCreate={vi.fn()} onPatch={onPatch} />);
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    expect(onPatch).toHaveBeenCalledWith(1, { status: "done" });
  });

  it("commits an edited numeric cell as a number on blur", () => {
    const onPatch = vi.fn();
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { progress: 0 })]} onCreate={vi.fn()} onPatch={onPatch} />);
    const cell = screen.getByLabelText("progress");
    fireEvent.change(cell, { target: { value: "40" } });
    fireEvent.blur(cell);
    expect(onPatch).toHaveBeenCalledWith(1, { progress: 40 });
  });

  it("shows the empty state when there are no records", () => {
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.getByText(/No issue records yet/)).toBeInTheDocument();
  });
});

describe("QuickCreate", () => {
  it("opens the form and creates with only the filled args", () => {
    const onCreate = vi.fn();
    // entities=[] so the only `title` input in the DOM is the create form's.
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[]} onCreate={onCreate} onPatch={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    fireEvent.change(screen.getByLabelText("title"), { target: { value: "Bug" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(onCreate).toHaveBeenCalledWith({ title: "Bug" });
  });
});

describe("role widgets in the table (§B3)", () => {
  const users = [
    { id: "alice", name: "Alice", section: "", email: "", photo_url: "" },
    { id: "bob", name: "Bob", section: "", email: "", photo_url: "" },
  ];
  const withActor: EntityType = { ...issueType, fields: [...issueType.fields, { name: "assignee", role: "actor" }] };

  it("edits an actor column as a directory select and patches the chosen id", () => {
    const onPatch = vi.fn();
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["assignee"] };
    render(
      <EntityViewBody spec={spec} type={withActor} entities={[issue(1, { assignee: "" })]} users={users} onCreate={vi.fn()} onPatch={onPatch} />,
    );
    fireEvent.change(screen.getByLabelText("assignee"), { target: { value: "bob" } });
    expect(onPatch).toHaveBeenCalledWith(1, { assignee: "bob" });
  });

  it("edits a daterange column as start + end date inputs", () => {
    const onPatch = vi.fn();
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["span"] };
    render(<EntityViewBody spec={spec} type={issueType} entities={[issue(1, { span: "" })]} onCreate={vi.fn()} onPatch={onPatch} />);
    fireEvent.change(screen.getByLabelText("span start"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("span end"), { target: { value: "2026-02-01" } });
    expect(onPatch).toHaveBeenLastCalledWith(1, { span: "2026-01-01/2026-02-01" });
  });

  it("renders a compute-on-read column read-only (no editable control)", () => {
    const withRollup: EntityType = { ...issueType, fields: [{ name: "open_count", role: "rollup" }] };
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["open_count"] };
    render(<EntityViewBody spec={spec} type={withRollup} entities={[issue(1, { open_count: 3 })]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.queryByLabelText("open_count")).not.toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders an actor field in quick-create as a directory select", () => {
    const withActorForm: EntityType = { ...issueType, form: [{ name: "assignee", widget: "actor", required: false }] };
    render(<EntityViewBody spec={tableSpec} type={withActorForm} entities={[]} users={users} onCreate={vi.fn()} onPatch={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(screen.getByLabelText("assignee").tagName).toBe("SELECT");
  });
});

describe("table sort / filter / column visibility (§A1)", () => {
  it("sorts rows case-insensitively when a column header is clicked", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title"] };
    render(
      <EntityViewBody
        spec={spec}
        type={issueType}
        entities={[issue(1, { title: "Beta" }), issue(2, { title: "alpha" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^title/ }));
    const values = screen.getAllByLabelText("title").map((i) => (i as HTMLInputElement).value);
    expect(values).toEqual(["alpha", "Beta"]);
  });

  it("filters rows by a status value from the role's value domain", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title", "status"] };
    render(
      <EntityViewBody
        spec={spec}
        type={issueType}
        entities={[issue(1, { title: "A", status: "open" }), issue(2, { title: "B", status: "done" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("filter status"), { target: { value: "done" } });
    expect(screen.queryByDisplayValue("A")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("B")).toBeInTheDocument();
  });

  it("hides a column through the columns menu", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title", "status"] };
    render(
      <EntityViewBody spec={spec} type={issueType} entities={[issue(1, { title: "A", status: "open" })]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /^status/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    fireEvent.click(screen.getByLabelText("toggle status"));
    expect(screen.queryByRole("button", { name: /^status/ })).not.toBeInTheDocument();
  });
});

describe("table multi-select + batch (§A1)", () => {
  const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title", "status"] };
  const two = [issue(1, { title: "A", status: "open" }), issue(2, { title: "B", status: "open" })];

  it("selects rows individually and via select-all", () => {
    render(<EntityViewBody spec={spec} type={issueType} entities={two} onCreate={vi.fn()} onPatch={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("select all"));
    expect(screen.getByLabelText("select 1")).toBeChecked();
    expect(screen.getByLabelText("select 2")).toBeChecked();
  });

  it("shows the batch toolbar only when at least one row is selected", () => {
    render(<EntityViewBody spec={spec} type={issueType} entities={two} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.queryByRole("toolbar", { name: "batch actions" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(screen.getByRole("toolbar", { name: "batch actions" })).toBeInTheDocument();
  });

  it("batch-sets a status on every selected row via N update calls (fan-out, §A1)", () => {
    const onPatch = vi.fn();
    render(<EntityViewBody spec={spec} type={issueType} entities={two} onCreate={vi.fn()} onPatch={onPatch} />);
    fireEvent.click(screen.getByLabelText("select all"));
    fireEvent.change(screen.getByLabelText("batch status"), { target: { value: "done" } });
    expect(onPatch).toHaveBeenCalledWith(1, { status: "done" });
    expect(onPatch).toHaveBeenCalledWith(2, { status: "done" });
    expect(onPatch).toHaveBeenCalledTimes(2);
  });

  it("clears the selection with the clear button", () => {
    render(<EntityViewBody spec={spec} type={issueType} entities={two} onCreate={vi.fn()} onPatch={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("select 1"));
    fireEvent.click(screen.getByRole("button", { name: /clear selection/i }));
    expect(screen.getByLabelText("select 1")).not.toBeChecked();
    expect(screen.queryByRole("toolbar", { name: "batch actions" })).not.toBeInTheDocument();
  });
});

describe("ref-traversal in the table (§A4)", () => {
  const refType: EntityType = {
    name: "issue",
    records_path: "issues",
    fields: [
      { name: "title", role: "text" },
      { name: "milestone", role: "ref", to: "milestone" },
    ],
    form: [],
  };
  const ms = (n: number, fields: Record<string, unknown>) => ({ number: n, type_name: "milestone", fields, body: "", diagnostics: [] });

  it("shows a milestone.title column as the referenced milestone's title", () => {
    const index = buildRefIndex({ milestone: [ms(5, { title: "v1.0" })] });
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["title", "milestone.title"] }}
        type={refType}
        entities={[issue(1, { title: "A", milestone: 5 })]}
        refIndex={index}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText("v1.0")).toBeInTheDocument();
  });

  it("degrades a dangling ref column to a marker instead of crashing (§D)", () => {
    const index = buildRefIndex({ milestone: [] });
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["milestone.title"] }}
        type={refType}
        entities={[issue(1, { milestone: 9 })]}
        refIndex={index}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText("#9?")).toBeInTheDocument();
  });

  it("edits a ref column as a #N-title picker and patches the chosen number", () => {
    const index = buildRefIndex({ milestone: [ms(5, { title: "v1.0" })] });
    const onPatch = vi.fn();
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["milestone"] }}
        type={refType}
        entities={[issue(1, { milestone: "" })]}
        refIndex={index}
        onCreate={vi.fn()}
        onPatch={onPatch}
      />,
    );
    fireEvent.change(screen.getByLabelText("milestone"), { target: { value: "5" } });
    expect(onPatch).toHaveBeenCalledWith(1, { milestone: 5 });
  });
});

describe("BoardView", () => {
  const boardSpec: ViewSpec = { view: "board", entity: "issue", group_by: "status", card: { title: "title", badges: ["progress"] } };

  it("renders a column per status value (including empty ones) and moves a card via its select", () => {
    const onPatch = vi.fn();
    render(<EntityViewBody spec={boardSpec} type={issueType} entities={[issue(1, { title: "A", status: "open" })]} onCreate={vi.fn()} onPatch={onPatch} />);
    // empty columns still render (from the field's closed vocabulary)
    expect(screen.getByTestId("col-in_progress")).toBeInTheDocument();
    expect(screen.getByTestId("col-done")).toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    expect(onPatch).toHaveBeenCalledWith(1, { status: "done" });
  });
});

describe("GanttView", () => {
  const ganttSpec: ViewSpec = { view: "gantt", entity: "issue", span: "span", label: "title" };

  it("draws a bar only for records that have a parseable span", () => {
    render(
      <EntityViewBody
        spec={ganttSpec}
        type={issueType}
        entities={[issue(1, { title: "A", span: "2026-01-01/2026-02-01" }), issue(2, { title: "B" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByTestId("bar-1")).toBeInTheDocument();
    expect(screen.queryByTestId("bar-2")).not.toBeInTheDocument();
  });

  it("shows a friendly note when no record has a date range", () => {
    render(<EntityViewBody spec={ganttSpec} type={issueType} entities={[issue(1, { title: "A" })]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.getByText(/No records with a date range/)).toBeInTheDocument();
  });
});

describe("conflict banner (§B2)", () => {
  it("shows a non-blocking alert for a conflicted record and dismisses it", () => {
    const onDismiss = vi.fn();
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[issue(1, { title: "A" })]}
        conflicts={[1]}
        onDismissConflict={onDismiss}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/changed/i);
    fireEvent.click(screen.getByLabelText("dismiss conflict 1"));
    expect(onDismiss).toHaveBeenCalledWith(1);
  });

  it("renders no alert when there are no conflicts", () => {
    render(
      <EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { title: "A" })]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("invalid records", () => {
  it("warns that unparseable records are hidden", () => {
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[issue(1, { title: "A" })]}
        invalid={[issue(2, {})]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/1 record couldn't be parsed/)).toBeInTheDocument();
  });
});

describe("fault-tolerant degradation (§D)", () => {
  it("shows an unparseable record as a degraded error row with its diagnostic", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title"] };
    const bad: EntityInstance = {
      number: 2,
      type_name: "issue",
      fields: {},
      body: "raw body text",
      diagnostics: [{ level: "error", message: "no frontmatter" }],
    };
    render(
      <EntityViewBody spec={spec} type={issueType} entities={[issue(1, { title: "A" })]} invalid={[bad]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.getByText(/no frontmatter/)).toBeInTheDocument();
  });

  it("marks a cell that carries a lint warning (warning → field)", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["status"] };
    const warned: EntityInstance = {
      number: 1,
      type_name: "issue",
      fields: { status: "weird" },
      body: "",
      diagnostics: [{ level: "warning", message: "status off vocab", field: "status" }],
    };
    render(<EntityViewBody spec={spec} type={issueType} entities={[warned]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.getByTitle("status off vocab")).toBeInTheDocument();
  });

  it("shows schema-level diagnostics as a banner (schema → panel)", () => {
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[]}
        catalogDiagnostics={[{ level: "error", message: "bad schema.yaml" }]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/bad schema\.yaml/)).toBeInTheDocument();
  });

  it("degrades to a no-schema note when the entity type has no schema", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title"] };
    render(<EntityViewBody spec={spec} type={null} entities={[issue(1, { title: "A" })]} schemaMissing onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.getByText(/no schema/i)).toBeInTheDocument();
  });
});
