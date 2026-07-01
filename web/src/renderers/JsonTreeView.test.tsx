// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { JsonTreeView } from "./JsonTreeView";

describe("JsonTreeView", () => {
  afterEach(cleanup);

  it("renders valid JSON as a tree showing top-level keys and values", () => {
    render(<JsonTreeView text='{"name": "widget", "qty": 3}' />);
    expect(screen.getByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/widget/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("falls back to raw text with a notice when JSON is malformed", () => {
    render(<JsonTreeView text='{"name": ' />);
    expect(screen.getByText(/couldn't parse/i)).toBeInTheDocument();
    // The raw bytes are still shown verbatim so nothing is lost.
    expect(screen.getByText(/\{"name":/)).toBeInTheDocument();
  });

  it("falls back to raw text with a notice when the file exceeds the byte cap", () => {
    render(<JsonTreeView text='{"a": 1}' maxBytes={4} />);
    expect(screen.getByText(/showing raw text/i)).toBeInTheDocument();
    expect(screen.getByText(/\{"a": 1\}/)).toBeInTheDocument();
  });
});
