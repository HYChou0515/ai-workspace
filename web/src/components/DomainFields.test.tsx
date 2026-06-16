// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../api/types";
import { DomainFields } from "./DomainFields";

afterEach(cleanup);

/** A lean manifest carrying only what DomainFields reads. */
const manifest = {
  layout: {
    breadcrumb: ["severity", "status"],
    statusbar: ["severity", "ghost", "product"],
    list: [],
  },
  fields: [
    { name: "severity", label: "Severity", kind: "select", options: ["P0", "P2"] },
    { name: "status", label: "Status", kind: "select", options: ["resolved"] },
    { name: "product", label: "Product", kind: "text" },
  ],
  field_styles: { severity: { P0: "err" } },
} as unknown as AppManifest;

const item = {
  resource_id: "rca-investigation/1",
  title: "Oven drift",
  owner: "alice",
  severity: "P0",
  status: "resolved",
  product: "MX-7 board",
} as AppItem;

describe("DomainFields", () => {
  it("renders a surface's layout fields in order, resolving tone from field_styles", () => {
    const { container } = render(
      <DomainFields surface="statusbar" manifest={manifest} item={item} />,
    );
    // severity → toned chip from field_styles
    expect(screen.getByText("P0")).toHaveAttribute("data-tone", "err");
    // product (text) renders its value
    expect(screen.getByText("MX-7 board")).toBeInTheDocument();
    // order follows layout.statusbar (severity before product)
    expect(container.textContent).toMatch(/P0.*MX-7 board/s);
  });

  it("skips a layout field name that has no FieldSpec (the 'ghost')", () => {
    render(<DomainFields surface="statusbar" manifest={manifest} item={item} />);
    expect(screen.queryByText("ghost")).not.toBeInTheDocument();
  });

  it("wires inline-edit per field when onEditField is given", () => {
    const onEditField = vi.fn();
    render(
      <DomainFields
        surface="breadcrumb"
        manifest={manifest}
        item={item}
        onEditField={onEditField}
      />,
    );
    fireEvent.click(screen.getByText("P0")); // severity chip → editor
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "P2" } });
    expect(onEditField).toHaveBeenCalledWith("severity", "P2");
  });
});
