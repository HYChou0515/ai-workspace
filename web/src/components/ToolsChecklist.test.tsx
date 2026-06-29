// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { ToolsChecklist } from "./ToolsChecklist";

afterEach(cleanup);

const TOOLS: ItemToolState[] = [
  { key: "exec", label: "Exec", description: "Run a shell command.", default_on: true, pref: "follow", effective: true },
  {
    key: "rca-tools",
    label: "RCA Tools",
    description: "Bundled tools: Spc, Pareto.",
    default_on: true,
    pref: "off",
    effective: false,
  },
];

describe("ToolsChecklist", () => {
  it("renders one row per tool with its human label", () => {
    render(<ToolsChecklist tools={TOOLS} prefs={{}} onChange={vi.fn()} />);
    expect(screen.getByText("Exec")).toBeInTheDocument();
    expect(screen.getByText("RCA Tools")).toBeInTheDocument();
  });

  it("reflects the current tri-state: an absent key is Follow, false is Off", () => {
    render(<ToolsChecklist tools={TOOLS} prefs={{ "rca-tools": false }} onChange={vi.fn()} />);
    expect(screen.getByTestId("tool-exec-follow")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("tool-rca-tools-off")).toHaveAttribute("aria-pressed", "true");
  });

  it("forcing a follow tool On emits onChange with the key pinned true", () => {
    const onChange = vi.fn();
    render(<ToolsChecklist tools={TOOLS} prefs={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("tool-exec-on"));
    expect(onChange).toHaveBeenCalledWith({ exec: true });
  });

  it("setting a pinned tool back to Follow drops the key from the override", () => {
    const onChange = vi.fn();
    render(<ToolsChecklist tools={TOOLS} prefs={{ "rca-tools": false }} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("tool-rca-tools-follow"));
    expect(onChange).toHaveBeenCalledWith({});
  });
});
