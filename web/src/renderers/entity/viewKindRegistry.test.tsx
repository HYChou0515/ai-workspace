// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { EntityViewProps, ViewSpec } from "./types";
import { resolveViewRenderer } from "./viewKindRegistry";

afterEach(cleanup);

const issueType: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "done"] },
    { name: "span", role: "daterange" },
  ],
  form: [{ name: "title", widget: "text", required: true }],
};

function issue(number: number, fields: Record<string, unknown>): EntityInstance {
  return { number, type_name: "issue", fields, body: "", diagnostics: [] };
}

function props(spec: ViewSpec, entities: EntityInstance[] = []): EntityViewProps {
  return { spec, type: issueType, entities, onCreate: vi.fn(), onPatch: vi.fn() };
}

describe("viewKindRegistry", () => {
  it("falls back to an unsupported-view notice for an unknown kind", () => {
    const r = resolveViewRenderer("chart");
    render(<r.Component {...props({ view: "chart" as never, entity: "issue" })} />);
    expect(screen.getByText(/unsupported view kind: chart/i)).toBeInTheDocument();
  });

  it("resolves the table kind to a renderer that draws a column grid", () => {
    const r = resolveViewRenderer("table");
    render(
      <r.Component
        {...props({ view: "table", entity: "issue", columns: ["title", "status"] }, [
          issue(1, { title: "A", status: "open" }),
        ])}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "title" })).toBeInTheDocument();
  });

  it("resolves the board kind to a renderer that draws status columns", () => {
    const r = resolveViewRenderer("board");
    render(
      <r.Component
        {...props({ view: "board", entity: "issue", group_by: "status" }, [
          issue(1, { title: "A", status: "open" }),
        ])}
      />,
    );
    expect(screen.getByTestId("col-done")).toBeInTheDocument();
  });

  it("resolves the gantt kind to a renderer that draws a bar per spanned record", () => {
    const r = resolveViewRenderer("gantt");
    render(
      <r.Component
        {...props({ view: "gantt", entity: "issue", span: "span", label: "title" }, [
          issue(1, { title: "A", span: "2026-01-01/2026-02-01" }),
        ])}
      />,
    );
    expect(screen.getByTestId("bar-1")).toBeInTheDocument();
  });
});
