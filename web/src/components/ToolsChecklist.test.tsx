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

  it("exposes the full label + description via title= so a clipped row is readable on hover (#456)", () => {
    // Pinned off → the row shows the tool's own description (not the follow hint),
    // and both the label and that description are single-line clipped with ellipsis.
    render(<ToolsChecklist tools={TOOLS} prefs={{ "rca-tools": false }} onChange={vi.fn()} />);
    expect(screen.getByText("RCA Tools")).toHaveAttribute("title", "RCA Tools");
    expect(screen.getByText("Bundled tools: Spc, Pareto.")).toHaveAttribute(
      "title",
      "Bundled tools: Spc, Pareto.",
    );
  });

  // ── where a tool came from (#724) ──────────────────────────────────

  it("names the package a command-granularity row belongs to", () => {
    // `app.json` may grant a whole bundle or one command of it, so two rows
    // can look like peers while one is part of the other. Told only "Spc", a
    // reader cannot tell which row's switch governs the tool they saw in chat.
    render(
      <ToolsChecklist
        tools={[{ ...TOOLS[0], key: "rca-tools:spc", label: "Spc", package: "Rca Tools" }]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-rca-tools:spc");
    expect(row).toHaveTextContent("Rca Tools");
    expect(row).toHaveTextContent("Spc");
  });

  it("shows a third-party tool's release and author on its own row", () => {
    render(
      <ToolsChecklist
        tools={[
          {
            ...TOOLS[0],
            key: "wafer-history",
            label: "Wafer History",
            external: true,
            version: "1.4.2",
            author: "Wafer Team <wafer@example.com>",
          },
        ]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-wafer-history");
    expect(row).toHaveTextContent("1.4.2");
    expect(row).toHaveTextContent("Wafer Team <wafer@example.com>");
  });

  it("says a first-party tool is the platform's own, rather than leaving it blank", () => {
    // Every row answers "who do I go to". A blank where other rows name a
    // person reads as missing information about the same kind of thing.
    render(<ToolsChecklist tools={[TOOLS[0]]} prefs={{}} onChange={vi.fn()} />);

    expect(screen.getByTestId("tool-row-exec")).toHaveTextContent("內建");
  });

  it("distinguishes a third-party tool whose author never filled their name in", () => {
    // NOT the same as "ours". Reading one off the absence of the other would
    // credit us with a stranger's code.
    render(
      <ToolsChecklist
        tools={[{ ...TOOLS[0], key: "wafer-history", external: true, version: "1.4.2" }]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-wafer-history");
    expect(row).toHaveTextContent("未註明作者");
    expect(row).not.toHaveTextContent("內建");
  });

  it("marks a row served from the cached copy", () => {
    render(
      <ToolsChecklist
        tools={[
          { ...TOOLS[0], key: "wafer-history", external: true, version: "1.4.2", stale: true },
        ]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByTestId("tool-wafer-history-stale")).toBeInTheDocument();
  });

  it("says why a declared tool could not be resolved, on the row that still has its switch", () => {
    render(
      <ToolsChecklist
        tools={[
          {
            ...TOOLS[0],
            key: "legacy-fetch",
            external: true,
            unavailable: "404 — the artifact expired",
          },
        ]}
        prefs={{}}
        onChange={vi.fn()}
      />,
    );

    const row = screen.getByTestId("tool-row-legacy-fetch");
    expect(row).toHaveTextContent("404 — the artifact expired");
    // And it claims nothing about a release or an author: nothing resolved,
    // so "no author published" would describe a manifest nobody read.
    expect(row).not.toHaveTextContent("未註明作者");
    expect(row).not.toHaveTextContent("內建");
  });
});
