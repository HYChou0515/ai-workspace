// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { JsonlView } from "./JsonlView";

describe("JsonlView", () => {
  afterEach(cleanup);

  it("renders one record card per non-empty line, skipping blank lines", () => {
    render(<JsonlView text={'{"a": 1}\n\n{"b": 2}\n'} />);
    expect(screen.getAllByTestId("jsonl-record")).toHaveLength(2);
    // Records are labelled by their 1-based source line number (blank lines
    // count so labels stay aligned with the file).
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows a bad line as raw text without dropping the sibling records", () => {
    render(<JsonlView text={'{"a": 1}\n{oops\n{"b": 2}\n'} />);
    // Two good records + the malformed one still render (3 cards).
    expect(screen.getAllByTestId("jsonl-record")).toHaveLength(3);
    expect(screen.getByText(/couldn't parse/i)).toBeInTheDocument();
    expect(screen.getByText(/\{oops/)).toBeInTheDocument();
  });

  it("caps at maxRecords and shows a truncation notice", () => {
    const text = Array.from({ length: 10 }, (_, i) => `{"i": ${i}}`).join("\n");
    render(<JsonlView text={text} maxRecords={3} />);
    expect(screen.getAllByTestId("jsonl-record")).toHaveLength(3);
    expect(screen.getByText(/showing first 3/i)).toBeInTheDocument();
  });

  it("shows an empty-file notice when there are no records", () => {
    render(<JsonlView text={"\n\n"} />);
    expect(screen.getByText("Empty file.")).toBeInTheDocument();
  });
});
