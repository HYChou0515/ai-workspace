// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { YamlTree } from "./YamlTree";

describe("YamlTree", () => {
  afterEach(cleanup);

  it("renders valid YAML as a tree showing keys and values", () => {
    render(<YamlTree text={"name: widget\nqty: 3\n"} />);
    expect(screen.getByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/widget/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("falls back to raw text with a notice when YAML is malformed", () => {
    render(<YamlTree text={"a: b: c\n"} />);
    expect(screen.getByText(/couldn't parse as yaml/i)).toBeInTheDocument();
    expect(screen.getByText(/a: b: c/)).toBeInTheDocument();
  });

  it("falls back to raw text when the file exceeds the byte cap", () => {
    render(<YamlTree text={"name: widget\n"} maxBytes={4} />);
    expect(screen.getByText(/showing raw text/i)).toBeInTheDocument();
  });
});
