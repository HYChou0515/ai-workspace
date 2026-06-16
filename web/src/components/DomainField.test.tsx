// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FieldSpec } from "../api/types";
import { DomainField } from "./DomainField";

afterEach(cleanup);

const severity: FieldSpec = {
  name: "severity",
  label: "Severity",
  kind: "select",
  options: ["P0", "P1", "P2", "P3", "P4"],
};
const product: FieldSpec = { name: "product", label: "Product", kind: "text" };

describe("DomainField (read-only)", () => {
  it("renders a select field's value as a toned chip", () => {
    render(<DomainField field={severity} value="P0" tone="err" />);
    expect(screen.getByText("P0")).toHaveAttribute("data-tone", "err");
  });

  it("renders a text field's value as plain text", () => {
    render(<DomainField field={product} value="MX-7 board" />);
    expect(screen.getByText("MX-7 board")).toBeInTheDocument();
  });
});

describe("DomainField (inline-edit)", () => {
  it("an editable select opens on click and commits the chosen option", () => {
    const onChange = vi.fn();
    render(<DomainField field={severity} value="P2" tone="warn" onChange={onChange} />);
    fireEvent.click(screen.getByText("P2")); // resting chip → open the editor
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "P0" } });
    expect(onChange).toHaveBeenCalledWith("P0");
  });

  it("an editable text field opens an input and commits on blur", () => {
    const onChange = vi.fn();
    render(<DomainField field={product} value="MX-7" onChange={onChange} />);
    fireEvent.click(screen.getByText("MX-7"));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "MX-8" } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith("MX-8");
  });

  it("stays read-only (no editor) when no onChange is given", () => {
    render(<DomainField field={severity} value="P2" tone="warn" />);
    fireEvent.click(screen.getByText("P2"));
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
