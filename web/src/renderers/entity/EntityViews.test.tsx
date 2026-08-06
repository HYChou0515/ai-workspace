// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityHealthFinding, EntityInstance, EntityType } from "../../api/entities";
import {
  EntityViewBody,
  fieldText,
  HealthView,
  parseSpan,
  parseViewSpec,
  QuickCreate,
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
  // #698 — the parser answers "is this a view file?" and nothing else. It used
  // to also own the list of kinds and the entity rule; both moved to the
  // registry, so a second-party kind can exist. The two cases below assert the
  // parser now KEEPS what it used to reject; that the user still gets told
  // (unsupported-kind notice / missing-entity banner) is asserted end-to-end in
  // viewKindPlugin.test.tsx, which goes through the file, not this function.
  it("rejects a doc with no view kind at all — that is not a view file", () => {
    expect(parseViewSpec("just: data\ncount: 3\n")).toBeNull();
    expect(parseViewSpec("view: ''\nentity: issue\n")).toBeNull();
  });
  it("keeps an unregistered view kind, leaving 'which kinds exist' to the registry (#698)", () => {
    expect(parseViewSpec("view: pie\nentity: issue\n")).toMatchObject({ view: "pie", entity: "issue" });
  });
  it("keeps a spec with no entity, leaving 'is an entity required' to the kind (#698)", () => {
    expect(parseViewSpec("view: table\n")).toMatchObject({ view: "table", entity: "" });
  });
  it("passes a plug-in's own top-level keys through verbatim (#698)", () => {
    expect(parseViewSpec("view: acme-wafer\nsource: /data/wafer.csv\n")).toMatchObject({
      view: "acme-wafer",
      source: "/data/wafer.csv",
    });
  });
  // #698 review: widening WHAT parses without widening what is VALIDATED handed
  // arbitrary user YAML straight into fields typed `string`. `title` is rendered
  // as a React child, so a mapping there threw and took the page down. Every
  // named field the platform reads must survive a hostile document.
  it("coerces the platform's own scalar fields, so a hostile document can't smuggle an object in", () => {
    const spec = parseViewSpec(
      ["view: table", "entity: issue", "title:", "  en: Hello", "  zh: 你好", "span: [1, 2]", "label: 3"].join("\n"),
    );
    expect(spec).not.toBeNull();
    expect(spec?.title).toBeUndefined();
    expect(spec?.span).toBeUndefined();
    expect(spec?.label).toBeUndefined();
  });
  it("drops a non-list `columns` instead of handing a renderer something it can't map over", () => {
    expect(parseViewSpec("view: table\nentity: issue\ncolumns: nope\n")?.columns).toBeUndefined();
    expect(parseViewSpec("view: table\nentity: issue\ncolumns: [a, 2, b]\n")?.columns).toEqual(["a", "b"]);
  });
  it("normalises multi-level sort rules, defaulting dir + dropping malformed (#GH-projects)", () => {
    const spec = parseViewSpec(
      "view: table\nentity: issue\nsort:\n  - { field: status, dir: desc }\n  - { field: title }\n  - { dir: asc }\n",
    );
    expect(spec?.sort).toEqual([
      { field: "status", dir: "desc" },
      { field: "title", dir: "asc" }, // dir defaulted; the field-less rule dropped
    ]);
  });
  it("treats an absent / non-array sort as manual order (undefined)", () => {
    expect(parseViewSpec("view: table\nentity: issue\n")?.sort).toBeUndefined();
    expect(parseViewSpec("view: table\nentity: issue\nsort: nonsense\n")?.sort).toBeUndefined();
  });
  it("keeps hidden_fields as a string list, dropping non-strings", () => {
    expect(parseViewSpec("view: table\nentity: issue\nhidden_fields: [due, 5, progress]\n")?.hidden_fields).toEqual([
      "due",
      "progress",
    ]);
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
    // The summary shows each level's count as its own chip (§F).
    expect(screen.getByText("1 error")).toBeInTheDocument();
    expect(screen.getByText("1 warning")).toBeInTheDocument();
    expect(screen.getByText("no frontmatter")).toBeInTheDocument();
    expect(screen.getByText(/status off/)).toBeInTheDocument();
  });

  it("shows an all-clear when there are no findings", () => {
    render(<HealthView findings={[]} />);
    expect(screen.getByText(/every record is valid/)).toBeInTheDocument();
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
  // #680 — the row's open handle is the `#N` cell. It CAN'T be a value column:
  // measured in real chromium (docs/plan-issue-680.md), a cell that swaps itself
  // for an input on the first click never receives the dblclick — the second
  // click lands on the input instead. `#N` is plain text, so it survives.
  it("opens the record on a double-click of the #N cell", () => {
    const onOpenRecord = vi.fn();
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[issue(3, { title: "Login broken", status: "open" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
        onOpenRecord={onOpenRecord}
      />,
    );

    fireEvent.doubleClick(screen.getByTestId("row-open-3"));

    expect(onOpenRecord).toHaveBeenCalledWith(3);
  });

  it("keeps inline editing on a single click — the two gestures don't collide", () => {
    const onOpenRecord = vi.fn();
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[issue(3, { title: "Login broken", status: "open" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
        onOpenRecord={onOpenRecord}
      />,
    );

    fireEvent.click(screen.getByLabelText("edit title"));

    // The cell became an input, and no modal was asked for.
    expect(screen.getByLabelText("title")).toBeInTheDocument();
    expect(onOpenRecord).not.toHaveBeenCalled();
  });

  it("renders a column per spec column plus the record number", () => {
    render(
      <EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { title: "Login broken", status: "open" })]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.getByRole("columnheader", { name: "title" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "status" })).toBeInTheDocument();
    // scalar cells show the value as text at rest (#3) — click to edit.
    expect(screen.getByLabelText("edit title")).toHaveTextContent("Login broken");
  });

  it("keeps a rank-role field out of the default columns (manual-order infra, #GH-projects)", () => {
    const typeWithRank: EntityType = {
      ...issueType,
      fields: [...issueType.fields, { name: "rank", role: "rank" }],
    };
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue" }} // no explicit columns → schema-derived
        type={typeWithRank}
        entities={[issue(1, { title: "A", status: "open", rank: 1 })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.queryByRole("columnheader", { name: "rank" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "title" })).toBeInTheDocument();
  });

  it("orders rows by the view's multi-level sort (#GH-projects P2)", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title", "status"], sort: [{ field: "status", dir: "asc" }] };
    render(
      <EntityViewBody
        spec={spec}
        type={issueType}
        entities={[issue(1, { title: "C", status: "done" }), issue(2, { title: "A", status: "open" }), issue(3, { title: "B", status: "in_progress" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    // status vocab order open < in_progress < done → A, B, C
    expect(screen.getAllByLabelText("edit title").map((el) => el.textContent)).toEqual(["A", "B", "C"]);
  });

  it("orders rows by the manual rank when the view has no sort (#GH-projects P2)", () => {
    const typeWithRank: EntityType = { ...issueType, fields: [...issueType.fields, { name: "rank", role: "rank" }] };
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["title"] }}
        type={typeWithRank}
        entities={[issue(1, { title: "C", rank: 3 }), issue(2, { title: "A", rank: 1 }), issue(3, { title: "B", rank: 2 })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getAllByLabelText("edit title").map((el) => el.textContent)).toEqual(["A", "B", "C"]);
  });

  it("gives each row a drag grip for manual reorder (#GH-projects)", () => {
    const typeWithRank: EntityType = { ...issueType, fields: [...issueType.fields, { name: "rank", role: "rank" }] };
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["title"] }}
        type={typeWithRank}
        entities={[issue(1, { title: "A", rank: 1 }), issue(2, { title: "B", rank: 2 })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    // a drag handle per row (the reorder OUTCOME is covered by tableDragResult unit tests)
    expect(screen.getByLabelText("drag 1")).toBeInTheDocument();
    expect(screen.getByLabelText("drag 2")).toBeInTheDocument();
  });

  it("hides the manual reorder grip when a sort or grouping is active (#GH-projects)", () => {
    const typeWithRank: EntityType = { ...issueType, fields: [...issueType.fields, { name: "rank", role: "rank" }] };
    const entities = [issue(1, { title: "A", status: "open", rank: 1 }), issue(2, { title: "B", status: "done", rank: 2 })];
    const { rerender } = render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["title", "status"], sort: [{ field: "title", dir: "asc" }] }}
        type={typeWithRank}
        entities={entities}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/drag \d+/)).not.toBeInTheDocument(); // sorted → no manual handle
    rerender(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["title", "status"], group_by: "status" }}
        type={typeWithRank}
        entities={entities}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/drag \d+/)).not.toBeInTheDocument(); // grouped → no manual handle
  });

  it("shows a value cell as text at rest and reveals the editor only on click (#3)", () => {
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { title: "A", status: "open" })]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    // no always-on native select at rest — just the value as text.
    expect(screen.queryByRole("combobox", { name: "status" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("edit status")).toHaveTextContent("open");
    // clicking the cell swaps in the shared editor.
    fireEvent.click(screen.getByLabelText("edit status"));
    expect(screen.getByLabelText("status").tagName).toBe("SELECT");
  });

  it("commits a status change through onPatch (the update write path)", () => {
    const onPatch = vi.fn();
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { status: "open" })]} onCreate={vi.fn()} onPatch={onPatch} />);
    fireEvent.click(screen.getByLabelText("edit status"));
    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    expect(onPatch).toHaveBeenCalledWith(1, { status: "done" });
  });

  it("commits an edited numeric cell as a number on blur", () => {
    const onPatch = vi.fn();
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { progress: 0 })]} onCreate={vi.fn()} onPatch={onPatch} />);
    fireEvent.click(screen.getByLabelText("edit progress"));
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

describe("table grouping (#GH-projects A)", () => {
  it("splits rows into collapsible group sections when group_by is set", () => {
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title"], group_by: "status" };
    render(
      <EntityViewBody
        spec={spec}
        type={issueType}
        entities={[issue(1, { title: "A", status: "open" }), issue(2, { title: "B", status: "done" })]}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByTestId("group-open")).toBeInTheDocument();
    expect(screen.getByTestId("group-done")).toBeInTheDocument();
    expect(screen.getAllByLabelText("edit title")).toHaveLength(2);
    // collapse the "open" group → its row disappears, the other stays
    fireEvent.click(screen.getByTestId("group-open"));
    expect(screen.getAllByLabelText("edit title")).toHaveLength(1);
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

  it("offers quick-create on a gantt view too, so a milestone/roadmap can add records", () => {
    // The gantt used to suppress + New (its inline create form was awkward); now
    // that create is a modal there's no reason to, and the Roadmap (a gantt of
    // milestones) had NO way to add a milestone at all.
    const spec: ViewSpec = { view: "gantt", entity: "issue", span: "span", label: "title" };
    render(<EntityViewBody spec={spec} type={issueType} entities={[]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.getByRole("button", { name: "+ New" })).toBeInTheDocument();
  });

  it("opens the create form in a modal dialog, not crammed into the header (#2)", () => {
    // #2: the expanded form used to live inside the header flex row, so on the
    // board it floated as a lopsided card beside a vertically-centred title. It
    // now opens in a ModalShell — the fields + Create action live in a dialog.
    render(<EntityViewBody spec={tableSpec} type={issueType} entities={[]} onCreate={vi.fn()} onPatch={vi.fn()} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("title")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create" })).toBeInTheDocument();
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
    fireEvent.click(screen.getByLabelText("edit assignee"));
    fireEvent.change(screen.getByLabelText("assignee"), { target: { value: "bob" } });
    expect(onPatch).toHaveBeenCalledWith(1, { assignee: "bob" });
  });

  it("edits a daterange column as start + end date inputs", () => {
    const onPatch = vi.fn();
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["span"] };
    render(<EntityViewBody spec={spec} type={issueType} entities={[issue(1, { span: "" })]} onCreate={vi.fn()} onPatch={onPatch} />);
    fireEvent.click(screen.getByLabelText("edit span"));
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

  it("renders a ref field in quick-create as a #N-title picker, not a raw number", () => {
    const withRef: EntityType = {
      name: "issue",
      records_path: "issues",
      fields: [{ name: "milestone", role: "ref", to: "milestone" }],
      form: [{ name: "milestone", widget: "ref", required: false }],
    };
    const index = buildRefIndex({
      milestone: [{ number: 5, type_name: "milestone", fields: { title: "v1.0" }, body: "", diagnostics: [] }],
    });
    render(<EntityViewBody spec={tableSpec} type={withRef} entities={[]} refIndex={index} onCreate={vi.fn()} onPatch={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    const sel = screen.getByLabelText("milestone");
    expect(sel.tagName).toBe("SELECT");
    expect(within(sel).getByRole("option", { name: "#5 v1.0" })).toBeInTheDocument();
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
    const values = screen.getAllByLabelText("edit title").map((el) => el.textContent);
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
    // titles show as text cells now (#3) — filter hides row A, keeps row B.
    expect(screen.queryByLabelText("edit title")).toHaveTextContent("B");
    expect(screen.getAllByLabelText("edit title")).toHaveLength(1);
  });

  it("hides a column named in the view's hidden_fields (#GH-projects P3)", () => {
    // Column show/hide now persists on the spec (set in the View panel) — a
    // hidden field simply doesn't render as a column.
    const spec: ViewSpec = { view: "table", entity: "issue", columns: ["title", "status"], hidden_fields: ["status"] };
    render(
      <EntityViewBody spec={spec} type={issueType} entities={[issue(1, { title: "A", status: "open" })]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.getByRole("columnheader", { name: "title" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "status" })).not.toBeInTheDocument();
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

  it("shows a plain milestone ref column as the referenced title at rest, not the raw number (#1)", () => {
    const index = buildRefIndex({ milestone: [ms(5, { title: "v1.0" })] });
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["title", "milestone"] }}
        type={refType}
        entities={[issue(1, { title: "A", milestone: 5 })]}
        refIndex={index}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    // the at-rest ref cell resolves 5 → "v1.0"; it must never show the bare id.
    expect(screen.getByLabelText("edit milestone")).toHaveTextContent("v1.0");
    expect(screen.getByLabelText("edit milestone")).not.toHaveTextContent("5");
  });

  it("degrades a plain dangling ref column to #N instead of a bare number", () => {
    const index = buildRefIndex({ milestone: [] });
    render(
      <EntityViewBody
        spec={{ view: "table", entity: "issue", columns: ["milestone"] }}
        type={refType}
        entities={[issue(1, { milestone: 9 })]}
        refIndex={index}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("edit milestone")).toHaveTextContent("#9");
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
    fireEvent.click(screen.getByLabelText("edit milestone"));
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
    // status shows as a chip at rest; click it to reveal the accessible select.
    fireEvent.click(screen.getByRole("button", { name: "edit status" }));
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

describe("read-only gate (§E canWrite)", () => {
  it("hides the create affordance and disables inline edits when canWrite is false", () => {
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[issue(1, { title: "A", status: "open" })]}
        canWrite={false}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "+ New" })).not.toBeInTheDocument();
    // §E — a read-only member sees plain text, not even a click-to-edit cell.
    expect(screen.queryByLabelText("edit status")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("edit title")).not.toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("hides multi-select + batch (§A1) when canWrite is false", () => {
    render(
      <EntityViewBody
        spec={tableSpec}
        type={issueType}
        entities={[issue(1, { title: "A", status: "open" })]}
        canWrite={false}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
      />,
    );
    // No selection checkboxes ⇒ the batch toolbar can never open.
    expect(screen.queryByLabelText("select all")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("select 1")).not.toBeInTheDocument();
  });

  it("shows write affordances by default (canWrite omitted ≡ writable)", () => {
    render(
      <EntityViewBody spec={tableSpec} type={issueType} entities={[issue(1, { title: "A" })]} onCreate={vi.fn()} onPatch={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "+ New" })).toBeInTheDocument();
    expect(screen.getByLabelText("edit title")).toBeInTheDocument();
    expect(screen.getByLabelText("select all")).toBeInTheDocument();
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

describe("QuickCreate defaults (#PM auto-schedule P9)", () => {
  it("opens with today already in the date range, and its end left open", () => {
    const today = new Date().toISOString().slice(0, 10);
    render(
      <QuickCreate
        form={[
          { name: "title", widget: "text", required: true },
          { name: "span", widget: "daterange", required: false },
        ]}
        onCreate={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(screen.getByLabelText("span start")).toHaveValue(today);
    expect(screen.getByLabelText("span end")).toHaveValue("");
  });
});
